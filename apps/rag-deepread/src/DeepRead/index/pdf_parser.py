from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List


def run_pdf_text_extract(
    input_path: Path,
    *,
    output_dir: Path,
    basename: str,
    min_text_chars: int = 20,
) -> Path:
    """Extract text from a digital PDF with PyMuPDF and write Markdown."""
    import fitz  # PyMuPDF

    merged_md_path = output_dir / f"{basename}.md"
    total_chars = 0
    doc = fitz.open(str(input_path))
    try:
        with merged_md_path.open("w", encoding="utf-8") as f_md:
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                if not text:
                    continue
                total_chars += len(text)
                f_md.write(f"## Page {page_num}\n\n")
                f_md.write(text)
                f_md.write("\n\n")
    finally:
        doc.close()

    if total_chars < min_text_chars:
        raise ValueError(
            f"PDF text extraction produced only {total_chars} chars; "
            "the file may be scanned or image-only"
        )
    return merged_md_path


def run_pdf_ocr(
    input_path: Path,
    *,
    output_dir: Path,
    basename: str,
    paddle_vl_rec_backend: str,
    paddle_vl_rec_server_url: str,
) -> Path:
    from paddleocr import PaddleOCRVL  # type: ignore

    pipeline = PaddleOCRVL(
        vl_rec_backend=paddle_vl_rec_backend,
        vl_rec_server_url=paddle_vl_rec_server_url,
    )
    output = pipeline.predict(str(input_path))

    merged_json_path = output_dir / f"{basename}.json"
    merged_md_path = output_dir / f"{basename}.md"
    temp_json_path = output_dir / f"{basename}_temp_page.json"
    temp_md_path = output_dir / f"{basename}_temp_page.md"

    all_json_data: List[Any] = []
    with merged_md_path.open("w", encoding="utf-8"):
        pass

    for idx, result in enumerate(output):
        try:
            result.save_to_json(save_path=str(temp_json_path))
            with temp_json_path.open("r", encoding="utf-8") as f_temp_json:
                all_json_data.append(json.load(f_temp_json))
        except Exception as exc:
            print(f"! Error processing JSON for page {idx + 1}: {exc}")

        try:
            result.save_to_markdown(save_path=str(temp_md_path))
            page_content = temp_md_path.read_text(encoding="utf-8")
            with merged_md_path.open("a", encoding="utf-8") as f_final_md:
                f_final_md.write(page_content)
                if idx < len(output) - 1:
                    f_final_md.write("\n\n")
        except Exception as exc:
            print(f"! Error processing Markdown for page {idx + 1}: {exc}")

    with merged_json_path.open("w", encoding="utf-8") as f_final_json:
        json.dump(all_json_data, f_final_json, indent=2, ensure_ascii=False)

    for temp_path in (temp_json_path, temp_md_path):
        if temp_path.exists():
            temp_path.unlink()

    return merged_md_path
