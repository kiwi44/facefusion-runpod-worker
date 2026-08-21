# FaceFusion Runpod worker

Runpod Serverless adapter for FaceFusion 3.8.2. It accepts private, short-lived
HTTPS media URLs, runs image or video face swaps, and uploads the result directly
to a caller-provided signed upload URL.

## Input

```json
{
  "input": {
    "operation": "swap",
    "media_type": "video",
    "source_url": "https://example.com/private-source.jpg",
    "target_url": "https://example.com/private-target.mp4",
    "face_mode": "reference",
    "face_index": 0,
    "face_swapper_model": "ghost_1_256",
    "reference_frame_number": 0,
    "output_upload": {
      "url": "https://example.com/signed-upload",
      "key": "users/user-id/results/job-id.mp4",
      "headers": { "Content-Type": "video/mp4" }
    }
  }
}
```

`face_mode` can be `one`, `reference`, or `many`. Faces are ordered left-to-right,
so a selected thumbnail index maps to `face_index`. Detection is available with
`{"operation":"detect","target_url":"..."}` or a `frame_data_url`.

For multi-face jobs, pass `face_mappings` with one distinct source URL per target
face, for example `[{"face_index":0,"source_url":"https://..."}]`. The worker
processes each assignment against the same image or video job in left-to-right
face order.

`face_swapper_model` is allowlisted to `hyperswap_1a_256`, `ghost_1_256`, and
`ghost_3_256`. If omitted, the worker uses `hyperswap_1a_256`. The Docker image
preloads all three models to avoid downloading model weights during a job.

The endpoint must be called through Runpod's authenticated `/run` or `/runsync`
API. Keep minimum workers at zero outside active testing to avoid idle GPU cost.

## Licensing

FaceFusion is distributed under OpenRAIL-AS. Individual bundled models carry
their own licenses; upstream currently labels several recognition and swap models
as non-commercial or research-only. Review and approve the complete model-license
chain before using this worker in a commercial product.
