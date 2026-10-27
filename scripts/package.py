#!/usr/bin/env python3
"""打包题目源码及验证摘要，排除构建产物、临时日志和缓存。"""
from pathlib import Path
import zipfile


def main():
    """生成ZIP并核验其中没有编译目录、缓存或本地运行证据目录。"""
    root = Path(__file__).resolve().parents[1]
    destination = root.parent / 'sap-order-sync.zip'
    excluded = {'target', '__pycache__', 'artifacts', '.git', '.idea', '.vscode'}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root)
            if not path.is_file() or excluded.intersection(relative.parts) or path.name == '.DS_Store' or path.suffix == '.iml':
                continue
            archive.write(path, str(relative))
    with zipfile.ZipFile(destination) as archive:
        assert all(not excluded.intersection(Path(name).parts) for name in archive.namelist())
        assert archive.testzip() is None
        assert {'task.toml', 'instruction.md', 'environment/Dockerfile',
                'environment/Dockerfile.db', 'environment/docker-compose.yaml',
                'environment/initdb/01-schema.sql', 'environment/initdb/02-seed.sql',
                'solution/solve.sh', 'tests/test.sh'}.issubset(archive.namelist())
        print(f'{destination}: {len(archive.namelist())} files, {destination.stat().st_size} bytes')


if __name__ == '__main__':
    main()
