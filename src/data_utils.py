"""
Helper for converting the 2018 Data Science Bowl (BBBC038) dataset format.

The raw Kaggle download has this structure:
  stage1_train/
    <image_id>/
      images/
        <image_id>.png
      masks/
        <mask_id>.png   (one PNG per nucleus instance, binary)

This script merges all instance masks into a single labeled mask per image.

Usage:
  python src/data_utils.py \
      --input /path/to/stage1_train \
      --output data/bbbc038_subset \
      --limit 50
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm


def combine_instance_masks(mask_dir: str | Path, output_path: str | Path) -> np.ndarray:
    mask_dir = Path(mask_dir)
    output_path = Path(output_path)

    labeled = None
    label_id = 1

    for mask_file in sorted(mask_dir.iterdir()):
        if mask_file.suffix.lower() not in {".png", ".tif", ".tiff", ".jpg"}:
            continue
        m = np.array(Image.open(mask_file).convert("L"))
        binary = m > 0
        if labeled is None:
            labeled = np.zeros(binary.shape, dtype=np.int32)
        labeled[binary & (labeled == 0)] = label_id
        label_id += 1

    if labeled is None:
        labeled = np.zeros((256, 256), dtype=np.int32)

    Image.fromarray(labeled.astype(np.uint16)).save(output_path)
    return labeled


def convert_dsb_dataset(
    input_dir: str | Path,
    output_dir: str | Path,
    limit: int | None = None,
) -> None:
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    images_out = output_dir / "images"
    masks_out = output_dir / "masks"
    images_out.mkdir(parents=True, exist_ok=True)
    masks_out.mkdir(parents=True, exist_ok=True)

    sample_dirs = sorted(p for p in input_dir.iterdir() if p.is_dir())
    if limit:
        sample_dirs = sample_dirs[:limit]

    print(f"Converting {len(sample_dirs)} samples from {input_dir} -> {output_dir}")

    for sample_dir in tqdm(sample_dirs):
        image_files = list((sample_dir / "images").glob("*.png"))
        if not image_files:
            continue

        img_src = image_files[0]
        img_dst = images_out / f"{sample_dir.name}.png"

        img = Image.open(img_src).convert("RGB")
        img.save(img_dst)

        mask_src_dir = sample_dir / "masks"
        if mask_src_dir.exists():
            mask_dst = masks_out / f"{sample_dir.name}.png"
            combine_instance_masks(mask_src_dir, mask_dst)

    print(f"Done. Images: {images_out}  Masks: {masks_out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert DSB/BBBC038 dataset to flat image/mask dirs.")
    parser.add_argument("--input", required=True, help="Path to stage1_train directory")
    parser.add_argument("--output", default="data/bbbc038_subset", help="Output directory")
    parser.add_argument("--limit", type=int, default=None, help="Max number of images to convert")
    args = parser.parse_args()

    convert_dsb_dataset(args.input, args.output, limit=args.limit)
