// A captured window enlarged to the overlay, by DLSS (nvngx_dlss.dll) or a plain
// filter: PresentWindowUpscale. A game in a small window really renders fewer pixels.
//
// A game hands DLSS a jittered low-resolution frame, its depth and its own motion
// vectors. Here there is only the finished picture: no jitter, a flat depth, and the
// optical flow the NR path computes. It cannot recover detail the capture does not hold.
// Trying it over a full-size capture, as DLAA and the upscaling types and as what enlarges
// NR's result under Boost, was slower and softer than the composite, and those are gone.
//
// DlssScale is also how interlaced NR's alternating mode scales its second input.

static const char kDlssScaleHlsl[] =
    "Texture2D<float4>   gSrc : register(t0);\n"
    "RWTexture2D<float4> gDst : register(u0);\n"
    "cbuffer SC : register(b0) { uint gW; uint gH; };\n"
    "SamplerState gSamp : register(s0);\n"
    "[numthreads(8, 8, 1)]\n"
    "void CSMain(uint3 id : SV_DispatchThreadID)\n"
    "{\n"
    "    if (id.x >= gW || id.y >= gH) return;\n"
    "    gDst[id.xy] = gSrc.SampleLevel(gSamp, (float2(id.xy) + 0.5f) / float2(gW, gH), 0);\n"
    "}\n";

static constexpr uint32_t kDlssWindowMode = 101u;   // g_dlss.mode while enlarging a window

static constexpr UINT kDlssPairs = 16;   // a pair per dispatch, round a ring (lens.inl)

static struct DlssState {
    NVSDK_NGX_Handle *feature = nullptr;
    NVSDK_NGX_Parameter *params = nullptr;
    ID3D12Resource *out = nullptr, *depth = nullptr, *color_in = nullptr, *mv_in = nullptr;
    uint32_t mode = 0;
    UINT w = 0, hgt = 0, rw = 0, rh = 0;
    bool failed = false;      // off for the process, and the log says why
    bool first = true;        // the next evaluate starts its history
    winrt::com_ptr<ID3D12RootSignature> root;
    winrt::com_ptr<ID3D12PipelineState> pso;
    winrt::com_ptr<ID3D12DescriptorHeap> heap;
    UINT ring = 0;
} g_dlss;

static void DlssRefuse(const char *why, unsigned code = 0)
{
    g_dlss.failed = true;
    Log("[dlss] %s (0x%08X) - the picture goes through without it", why, code);
}

static void CloseDlss()
{
    auto &s = g_dlss;
    if (g_submission_failed) { s.feature = nullptr; return; }
    if ((s.feature != nullptr || s.out != nullptr) && h.fence && h.fence_value != 0 &&
        !WaitFenceValue(h.fence, h.fence_value, 30000, "dlss-close"))
        return;
    if (s.feature != nullptr) { NVSDK_NGX_D3D12_ReleaseFeature(s.feature); s.feature = nullptr; }
    for (ID3D12Resource **r : {&s.out, &s.depth, &s.color_in, &s.mv_in})
        if (*r != nullptr) { (*r)->Release(); *r = nullptr; }
    s.w = s.hgt = s.rw = s.rh = 0;
    s.first = true;
}

static bool DlssPipeline()
{
    auto &s = g_dlss;
    if (s.pso) return true;
    D3D12_DESCRIPTOR_RANGE ranges[2] = {
        {D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 1, 0, 0, 0},
        {D3D12_DESCRIPTOR_RANGE_TYPE_UAV, 1, 0, 0, 1}};
    D3D12_ROOT_PARAMETER roots[2] = {};
    roots[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    roots[0].Constants = {0, 0, 2};
    roots[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    roots[1].DescriptorTable = {2, ranges};
    D3D12_STATIC_SAMPLER_DESC samp = {};
    samp.Filter = D3D12_FILTER_MIN_MAG_MIP_LINEAR;
    samp.AddressU = samp.AddressV = samp.AddressW = D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    samp.MaxLOD = D3D12_FLOAT32_MAX;
    samp.ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    D3D12_ROOT_SIGNATURE_DESC rd = {2, roots, 1, &samp, D3D12_ROOT_SIGNATURE_FLAG_NONE};
    winrt::com_ptr<ID3DBlob> code, errors;
    if (FAILED(D3D12SerializeRootSignature(&rd, D3D_ROOT_SIGNATURE_VERSION_1, code.put(), errors.put())) ||
        FAILED(h.dev->CreateRootSignature(0, code->GetBufferPointer(), code->GetBufferSize(),
                                          IID_PPV_ARGS(s.root.put()))))
    { DlssRefuse("root signature failed"); return false; }
    code = nullptr; errors = nullptr;
    if (FAILED(D3DCompile(kDlssScaleHlsl, sizeof(kDlssScaleHlsl) - 1, "dlss_scale.hlsl", nullptr, nullptr,
                          "CSMain", "cs_5_0", 0, 0, code.put(), errors.put())))
    { DlssRefuse("scale shader failed"); return false; }
    D3D12_COMPUTE_PIPELINE_STATE_DESC pd = {};
    pd.pRootSignature = s.root.get();
    pd.CS = {code->GetBufferPointer(), code->GetBufferSize()};
    D3D12_DESCRIPTOR_HEAP_DESC hd = {D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, 2 * kDlssPairs,
                                     D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE, 0};
    if (FAILED(h.dev->CreateComputePipelineState(&pd, IID_PPV_ARGS(s.pso.put()))) ||
        FAILED(h.dev->CreateDescriptorHeap(&hd, IID_PPV_ARGS(s.heap.put()))))
    { s.pso = nullptr; DlssRefuse("scale pipeline failed"); return false; }
    return true;
}

static ID3D12Resource *DlssTex(UINT w, UINT hgt, DXGI_FORMAT fmt, bool uav,
                               D3D12_RESOURCE_STATES state)
{
    D3D12_HEAP_PROPERTIES def = {};
    def.Type = D3D12_HEAP_TYPE_DEFAULT;
    D3D12_RESOURCE_DESC td = {};
    td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width = w; td.Height = hgt; td.DepthOrArraySize = 1; td.MipLevels = 1;
    td.Format = fmt; td.SampleDesc.Count = 1;
    td.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
    td.Flags = uav ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS : D3D12_RESOURCE_FLAG_NONE;
    ID3D12Resource *t = nullptr;
    // Committed resources come zeroed: the flat depth is that zero.
    if (FAILED(h.dev->CreateCommittedResource(&def, D3D12_HEAP_FLAG_NONE, &td, state, nullptr,
                                              IID_PPV_ARGS(&t))))
        return nullptr;
    return t;
}

// src and dst rest in NON_PIXEL_SHADER_RESOURCE, before and after.
static void DlssScale(ID3D12Resource *src, ID3D12Resource *dst, UINT w, UINT hgt)
{
    auto &s = g_dlss;
    const UINT stride = h.dev->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    const UINT pair = s.ring++ % kDlssPairs;
    D3D12_CPU_DESCRIPTOR_HANDLE cpu = s.heap->GetCPUDescriptorHandleForHeapStart();
    cpu.ptr += static_cast<SIZE_T>(pair) * 2u * stride;
    D3D12_SHADER_RESOURCE_VIEW_DESC sd = {};
    sd.Format = src->GetDesc().Format;
    sd.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    sd.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    sd.Texture2D.MipLevels = 1;
    h.dev->CreateShaderResourceView(src, &sd, cpu);
    cpu.ptr += stride;
    D3D12_UNORDERED_ACCESS_VIEW_DESC ud = {};
    ud.Format = dst->GetDesc().Format;
    ud.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
    h.dev->CreateUnorderedAccessView(dst, nullptr, &ud, cpu);
    auto to_uav = Transition(dst, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
                             D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    h.list->ResourceBarrier(1, &to_uav);
    ID3D12DescriptorHeap *heaps[] = {s.heap.get()};
    h.list->SetDescriptorHeaps(1, heaps);
    h.list->SetComputeRootSignature(s.root.get());
    h.list->SetPipelineState(s.pso.get());
    const UINT rc[2] = {w, hgt};
    h.list->SetComputeRoot32BitConstants(0, 2, rc, 0);
    D3D12_GPU_DESCRIPTOR_HANDLE gpu = s.heap->GetGPUDescriptorHandleForHeapStart();
    gpu.ptr += static_cast<UINT64>(pair) * 2u * stride;
    h.list->SetComputeRootDescriptorTable(1, gpu);
    h.list->Dispatch((w + 7) / 8, (hgt + 7) / 8, 1);
    auto to_srv = Transition(dst, D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                             D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    h.list->ResourceBarrier(1, &to_srv);
}

// These two hold no C++ objects: __try does not sit with unwinding.
static NVSDK_NGX_Result DlssCreateCall(NVSDK_NGX_DLSS_Create_Params *cp, DWORD *seh)
{
    *seh = 0;
    NVSDK_NGX_Result r = NVSDK_NGX_Result_Fail;
    __try { r = NGX_D3D12_CREATE_DLSS_EXT(h.list, 1, 1, &g_dlss.feature, g_dlss.params, cp); }
    __except (EXCEPTION_EXECUTE_HANDLER) { *seh = GetExceptionCode(); }
    return r;
}

static NVSDK_NGX_Result DlssEvaluateCall(NVSDK_NGX_D3D12_DLSS_Eval_Params *ep, DWORD *seh)
{
    *seh = 0;
    NVSDK_NGX_Result r = NVSDK_NGX_Result_Fail;
    __try { r = NGX_D3D12_EVALUATE_DLSS_EXT(h.list, g_dlss.feature, g_dlss.params, ep); }
    __except (EXCEPTION_EXECUTE_HANDLER) { *seh = GetExceptionCode(); }
    return r;
}

static bool DlssParams()
{
    auto &s = g_dlss;
    if (s.params != nullptr) return true;
    const NVSDK_NGX_Result r = NVSDK_NGX_D3D12_GetCapabilityParameters(&s.params);
    if (NVSDK_NGX_FAILED(r) || s.params == nullptr)
    { DlssRefuse("NGX gave no parameters", static_cast<unsigned>(r)); return false; }
    int available = 0;
    if (NVSDK_NGX_FAILED(s.params->Get(NVSDK_NGX_Parameter_SuperSampling_Available, &available)) ||
        available == 0)
    {
        int why = 0;
        s.params->Get(NVSDK_NGX_Parameter_SuperSampling_FeatureInitResult, &why);
        DlssRefuse("Super Resolution is not available here - is native\\nvngx_dlss.dll there?",
                   static_cast<unsigned>(why));
        return false;
    }
    return true;
}

// The captured window, enlarged to the overlay and shown (NS_WINDOW_UPSCALE): NR's
// result on an NR frame, else the capture with the camera on it.
static bool PresentWindowUpscale(VideoState &v, bool bypass)
{
    auto &s = g_dlss;
    const UINT cw = v.upscale ? v.full_w : v.w, ch = v.upscale ? v.full_h : v.hgt;
    const UINT mw = g_win_up_w, mh = g_win_up_h;
    if (s.mode != kDlssWindowMode || s.w != mw || s.hgt != mh || s.rw != cw || s.rh != ch)
    {
        CloseDlss();
        if (g_submission_failed) return false;
        s.mode = kDlssWindowMode;
    }
    const bool use_dlss = !g_win_up_bilinear && !s.failed && DlssParams();
    if (s.out == nullptr)
    {
        if (!DlssPipeline()) return false;
        s.out = DlssTex(mw, mh, v.color.tex->GetDesc().Format, true,
                        D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        s.depth = DlssTex(cw, ch, DXGI_FORMAT_R32_FLOAT, false,
                          D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        if (s.out == nullptr || s.depth == nullptr) { Log("[dlss] test: textures failed"); return false; }
        s.w = mw; s.hgt = mh; s.rw = cw; s.rh = ch;
    }
    if (!BeginCommands()) return false;
    DWORD seh = 0;
    bool done = false;
    // A resource at rest in the state the rest of this reads it in.
    ID3D12Resource *source = bypass
        ? LensApply(h.list, v.color.tex, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, LENS_SHOWN)
        : v.output;
    if (!bypass)
    {
        auto to_read = Transition(source, D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                                  D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        h.list->ResourceBarrier(1, &to_read);
    }
    if (use_dlss)
    {
        if (s.feature == nullptr)
        {
            NVSDK_NGX_DLSS_Create_Params cp = {};
            cp.Feature.InWidth = cw;
            cp.Feature.InHeight = ch;
            cp.Feature.InTargetWidth = mw;
            cp.Feature.InTargetHeight = mh;
            cp.Feature.InPerfQualityValue = NVSDK_NGX_PerfQuality_Value_MaxPerf;
            cp.InFeatureCreateFlags = NVSDK_NGX_DLSS_Feature_Flags_MVLowRes |
                                      NVSDK_NGX_DLSS_Feature_Flags_AutoExposure;
            const NVSDK_NGX_Result r = DlssCreateCall(&cp, &seh);
            if (seh != 0) { AbortCommands(); s.feature = nullptr; DlssRefuse("create faulted", seh); return false; }
            if (NVSDK_NGX_FAILED(r) || s.feature == nullptr)
            { s.feature = nullptr; DlssRefuse("create refused for the window", static_cast<unsigned>(r)); }
            else
            {
                s.first = true;
                Log("[dlss] test: enlarging the window, %ux%u -> %ux%u", cw, ch, mw, mh);
            }
        }
        if (s.feature != nullptr)
        {
            auto to_uav = Transition(s.out, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
                                     D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
            h.list->ResourceBarrier(1, &to_uav);
            NVSDK_NGX_D3D12_DLSS_Eval_Params ep = {};
            ep.Feature.pInColor = source;
            ep.Feature.pInOutput = s.out;
            ep.pInDepth = s.depth;
            ep.pInMotionVectors = v.mv.tex;
            ep.InRenderSubrectDimensions.Width = cw;
            ep.InRenderSubrectDimensions.Height = ch;
            ep.InReset = s.first ? 1 : 0;
            ep.InMVScaleX = static_cast<float>(cw) / static_cast<float>(v.w);
            ep.InMVScaleY = static_cast<float>(ch) / static_cast<float>(v.hgt);
            ep.InPreExposure = 1.0f;
            const NVSDK_NGX_Result r = DlssEvaluateCall(&ep, &seh);
            if (seh != 0) { AbortCommands(); DlssRefuse("evaluate faulted", seh); return false; }
            if (NVSDK_NGX_FAILED(r)) DlssRefuse("evaluate refused", static_cast<unsigned>(r));
            else { s.first = false; done = true; }
            auto to_srv = Transition(s.out, D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                                     D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
            h.list->ResourceBarrier(1, &to_srv);
        }
    }
    if (!done) DlssScale(source, s.out, mw, mh);
    // Spout and the recording take the window at its own size.
    auto to_copy = Transition(source, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE,
                              D3D12_RESOURCE_STATE_COPY_SOURCE);
    h.list->ResourceBarrier(1, &to_copy);
    ExportCopy(h.list, source, cw, ch);
    auto back = Transition(source, D3D12_RESOURCE_STATE_COPY_SOURCE,
                           bypass ? D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE
                                  : D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    h.list->ResourceBarrier(1, &back);
    ID3D12Resource *shown = BadgeApply(h.list, s.out, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    const UINT64 fence = EndCommands();
    if (fence == 0 || !WaitFenceValue(h.fence, fence, 2000, "window-upscale")) return false;
    ID3D12Resource *bb = nullptr;
    const HRESULT got = g_present_swap->GetBuffer(g_present_swap->GetCurrentBackBufferIndex(),
                                                  __uuidof(ID3D12Resource), reinterpret_cast<void **>(&bb));
    if (FAILED(got) || bb == nullptr) return FailGpuWork("present", "get-buffer-error", FAILED(got) ? got : E_POINTER);
    if (!CopyToBackBuffer(bb, shown, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, "window-upscale"))
        return false;
    const bool ok = PresentStatus(g_present_swap->Present(0, 0), "present");
    if (!ok) return false;
    bb->Release();
    RevealOnFirstPresent();
    return true;
}

