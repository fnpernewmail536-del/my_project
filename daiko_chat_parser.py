"""Compatibility entry point for the shared, section-aware preview parser."""
from local_chat.structured_requests import parse_document as parse

if __name__ == '__main__':
    import json, sys
    print(json.dumps(parse(sys.stdin.read()), ensure_ascii=False, indent=2))
