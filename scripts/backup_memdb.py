"""ChromaDB 数据备份脚本（轻量 wrapper）

用法:
    python scripts/backup_memdb.py                    # 默认备份到 memdb/backups/
    python scripts/backup_memdb.py --target D:/backups # 指定目标目录
    python scripts/backup_memdb.py --list              # 列出已有备份

核心逻辑已迁移到 memos.features.backup，本脚本保留为便捷入口。
使用 CLI 命令 `memos backup` 为推荐方式。
"""

import argparse
import logging
from pathlib import Path

from memos.features.backup import backup_memdb, list_backups

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="ChromaDB 数据备份")
    parser.add_argument("--target", type=str, default=None, help="备份目标目录")
    parser.add_argument("--list", dest="list_backups", action="store_true", help="列出已有备份")
    args = parser.parse_args()

    if args.list_backups:
        result = list_backups(args.target)
        backups = result["backups"]
        if not backups:
            print("暂无备份")
        else:
            print(f"备份目标: {result['target_dir']}")
            print(f"备份总数: {result['total']} (最大保留: {result['max_backups']})")
            for b in backups:
                import datetime

                ts_str = datetime.datetime.fromtimestamp(b["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                size_mb = b.get("size_bytes", 0) / (1024 * 1024)
                status_label = "✓" if b.get("status") == "complete" else "⚠" if b.get("status") == "partial" else "✗"
                print(f"  {status_label} {b['id']}  {ts_str}  {size_mb:.2f} MB  {b.get('file_count', '?')} 文件")
        return

    target = Path(args.target) if args.target else None
    result = backup_memdb(args.target)
    print(f"备份完成: {result['path']} ({result['size_mb']} MB, {result['file_count']} 文件)")


if __name__ == "__main__":
    main()
