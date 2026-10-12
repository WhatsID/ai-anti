"""Lossless LF-delimited review blocks and their source provenance."""
import os

from review_material import MaterialError, MAX_STAGE_BYTES, read_text, sha

MAX_BLOCK_BYTES = 12 * 1024
MAX_BLOCK_LINES = 120


def physical_lines(data):
    """Split only on LF; retain LF on its preceding line, never add a tail."""
    parts = data.split(b'\n')
    lines = [part + b'\n' for part in parts[:-1]]
    if parts[-1] or not lines:
        lines.append(parts[-1])
    return lines


def split_blocks(data):
    blocks, current, size, start, count = [], [], 0, 1, 0
    for line in physical_lines(data):
        if len(line) > MAX_BLOCK_BYTES:
            raise MaterialError('physical line exceeds block byte limit')
        if current and (size + len(line) > MAX_BLOCK_BYTES or count == MAX_BLOCK_LINES):
            blocks.append((b''.join(current), start, start + count - 1))
            start += count
            current, size, count = [], 0, 0
        current.append(line)
        size += len(line)
        count += 1
    blocks.append((b''.join(current), start, start + count - 1))
    if b''.join(block[0] for block in blocks) != data:
        raise MaterialError('block reconstruction mismatch')
    return blocks


def chunk_file(stage, name, source_id=1):
    if type(source_id) is not int or source_id < 1:
        raise MaterialError("invalid source id")
    source = stage / name
    data = read_text(source, name)
    blocks = split_blocks(data)
    records = []
    for index, (content, start, end) in enumerate(blocks, 1):
        target = 'chunks/%03d/%03d.txt' % (source_id, index)
        path = stage / target
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(content)
        os.chmod(path, 0o600)
        records.append({'path': target, 'sha256': sha(content), 'bytes': len(content),
                        'start_line': start, 'end_line': end})
    restored = b''.join((stage / item['path']).read_bytes() for item in records)
    if restored != data or sha(restored) != sha(data):
        raise MaterialError('source reconstruction mismatch: ' + name)
    source.unlink()
    return {'sha256': sha(data), 'bytes': len(data), 'blocks': records}


def chunk_stage(stage):
    paths = sorted(str(path.relative_to(stage)) for path in stage.rglob('*') if path.is_file())
    if sum((stage / name).stat().st_size for name in paths) > MAX_STAGE_BYTES:
        raise MaterialError('stage snapshot exceeds 2MiB')
    sources = {name: chunk_file(stage, name, source_id)
               for source_id, name in enumerate(paths, 1)}
    if sum(item['bytes'] for source in sources.values() for item in source['blocks']) > MAX_STAGE_BYTES:
        raise MaterialError('chunked stage snapshot exceeds 2MiB')
    return sources


def required_files(sources):
    return [block['path'] for source in sources.values() for block in source['blocks']]


def source_labels(sources):
    return {block['path']: '%s (lines %s-%s; %s)' % (
        name, block['start_line'], block['end_line'], block['path'])
        for name, source in sources.items() for block in source['blocks']}
