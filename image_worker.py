"""Run in a separate process: release GPU memory completely after generation."""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('HF_HOME', str(ROOT / 'models' / 'huggingface'))
os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    import torch
    from diffusers import AutoPipelineForText2Image
    model = ROOT / 'models' / 'sd-turbo'
    source = str(model) if (model / 'model_index.json').is_file() else 'stabilityai/sd-turbo'
    pipe = AutoPipelineForText2Image.from_pretrained(source, torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                                                   variant='fp16', local_files_only=not args.download)
    if args.download:
        print('Model downloaded', flush=True)
        return
    pipe.to('cuda' if torch.cuda.is_available() else 'cpu')
    pipe.enable_attention_slicing()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    for item in manifest:
        generator = torch.Generator(device='cuda' if torch.cuda.is_available() else 'cpu').manual_seed(item['seed'])
        result = pipe(prompt=item['prompt'], num_inference_steps=3, guidance_scale=0.0,
                      width=768, height=512, generator=generator)
        if result.nsfw_content_detected and any(result.nsfw_content_detected):
            raise RuntimeError('Модель отклонила иллюстрацию. Измените тему или описание сцены.')
        result.images[0].save(item['path'])
    print('Images saved', flush=True)


if __name__ == '__main__':
    main()
