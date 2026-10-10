"""Isolated bounded PDF text extraction. No OCR or action execution."""
import json
from pathlib import Path
import resource
import sys
from pypdf import PdfReader


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    # macOS does not support lowering DATA this way. Keep CPU/timeout/file/page
    # limits there; Linux additionally enforces an address-space ceiling.
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    path = Path(sys.argv[1])
    if path.stat().st_size > 16_000_000:
        raise ValueError('PDF exceeds configured file limit')
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError('Encrypted or oversized document requires separate review')
    pages = []
    total = 0
    for number, page in enumerate(reader.pages, 1):
        content = page.get_contents()
        if content and len(content.get_data()) > 8_000_000:
            raise ValueError('PDF page exceeds processing limit')
        text = page.extract_text() or ''
        total += len(text)
        if total > 500_000:
            raise ValueError('PDF extracted text exceeds processing limit')
        pages.append({'page': number, 'text': text, 'status': 'extracted' if text.strip() else 'empty_or_scan_requires_review'})
    print(json.dumps({'pages': pages, 'parser_version': 'pypdf-isolated-v1', 'ocr': False}))


if __name__ == '__main__':
    main()
