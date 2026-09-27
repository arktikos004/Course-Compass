#!/usr/bin/env python3
"""Course Master - 智慧選課輔助系統主入口點"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.common import setup_logging

def main():
    parser = argparse.ArgumentParser(description="Course Master - 智慧選課輔助系統")
    parser.add_argument(
        "command",
        choices=["crawl", "process", "build-dict", "api", "all",
                 # AI 功能（需 pip install -r requirements-ai.txt）
                 "train-demand", "eval-holdout", "predict-demand",
                 "fetch-syllabi", "build-index", "eval-agent", "eval-rag"],
        help="要執行的命令"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日誌級別"
    )
    parser.add_argument(
        "--latest",
        type=int,
        default=None,
        metavar="N",
        help="爬蟲只抓最後 N 個學期（過去學期資料已凍結，排程更新建議用 --latest 2）"
    )
    parser.add_argument(
        "--semester", metavar="YYY-S",
        help="fetch-syllabi / build-index：指定學期（預設為資料中最新學期）"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="fetch-syllabi：最多下載幾份（測試用）"
    )

    args = parser.parse_args()

    import logging
    logging.basicConfig(level=getattr(logging, args.log_level))

    if args.command == "crawl":
        from crawler.crawler import main as crawl_main
        crawl_main(only_latest=args.latest)

    elif args.command == "process":
        from processor.data_processor import main as process_main
        process_main()

    elif args.command == "build-dict":
        from processor.teacher_dict_builder import main as dict_main
        dict_main()

    elif args.command == "api":
        from api.app import main as api_main
        api_main()

    elif args.command == "train-demand":
        from ml.cli import train_main
        train_main()

    elif args.command == "eval-holdout":
        from ml.cli import holdout_main
        holdout_main()

    elif args.command == "predict-demand":
        from ml.cli import predict_main
        predict_main()

    elif args.command == "fetch-syllabi":
        from ai.syllabus import main as syllabus_main
        syllabus_main(args.semester, args.limit)

    elif args.command == "build-index":
        from ai.index import main as index_main
        index_main(args.semester)

    elif args.command == "eval-agent":
        from ai.evaluate import main as eval_main
        eval_main()

    elif args.command == "eval-rag":
        from ai.evaluate import main_rag
        main_rag()

    elif args.command == "all":
        print("開始執行完整流程...")
        try:
            print("1. 爬取課程數據...")
            from crawler.crawler import main as crawl_main
            crawl_main(only_latest=args.latest)

            print("2. 構建教師字典...")
            from processor.teacher_dict_builder import main as dict_main
            dict_main()

            print("3. 處理課程數據...")
            from processor.data_processor import main as process_main
            process_main()

            print("4. 啟動 API 服務器...")
            from api.app import main as api_main
            api_main()

        except Exception as e:
            print(f"執行失敗: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
