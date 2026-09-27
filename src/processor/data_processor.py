"""資料處理工具 - 讀取爬蟲產生的原始 CSV，輸出已清理且扁平化的資料集"""

import pandas as pd
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
import logging

from config import RAW_DATA_DIR, PROCESSED_DATA_DIR, TEACHER_DICT_PATH
from utils.common import (
    extract_year_semester_from_filename, safe_read_csv, safe_write_csv,
    get_timestamp
)
from .department_mapper import DepartmentMapper

class DataProcessor:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.department_mapper = DepartmentMapper()

    @staticmethod
    def parse_schedule_location(schedule_str: str) -> List[Dict[str, Any]]:
        """解析節次與地點"""
        if pd.isna(schedule_str) or str(schedule_str).strip() == '未知':
            return [{'星期': pd.NA, '起始節次': pd.NA, '結束節次': pd.NA, '上課地點': pd.NA}]

        text = str(schedule_str).strip()
        pattern = r'(\([一二三四五六日]\))\s*([\d,\-]+)\s*(.*?)(?=\s*\([一二三四五六日]\)|$)'
        matches = re.findall(pattern, text)

        if not matches:
            return [{'星期': pd.NA, '起始節次': pd.NA, '結束節次': pd.NA, '上課地點': text}]

        results = []
        for w, t, l in matches:
            start_node = pd.NA
            end_node = pd.NA
            try:
                nums = re.findall(r'\d+', t)
                if nums:
                    start_node = int(nums[0])
                    end_node = int(nums[-1])
            except (ValueError, IndexError):
                pass

            results.append({
                '星期': w,
                '起始節次': start_node,
                '結束節次': end_node,
                '上課地點': l.strip()
            })
        return results

    def load_teacher_set(self, dict_path: Path) -> Tuple[Set[str], int]:
        """載入教師字典"""
        if not dict_path.exists():
            self.logger.warning(f"找不到字典檔：{dict_path}，將無法正確拆分黏連姓名。")
            return set(), 4

        df = safe_read_csv(dict_path)
        if df is None or 'teacher_name' not in df.columns:
            return set(), 4

        t_set = set(df['teacher_name'].dropna().astype(str))
        max_name_len = max([len(n) for n in t_set]) if t_set else 4
        return t_set, max_name_len

    @staticmethod
    def split_teachers_by_dict(text: str, teacher_set: Set[str], max_name_len: int) -> List[str]:
        """教師姓名拆分（最大匹配）"""
        result = []
        n = len(text)
        i = 0
        loop_limit = n * 2
        count = 0

        while i < n:
            matched = None
            window = min(n - i, max_name_len)
            for width in range(window, 1, -1):
                candidate = text[i : i + width]
                if candidate in teacher_set:
                    matched = candidate
                    break

            if matched:
                result.append(matched)
                i += len(matched)
            else:
                i += 1 
            
            count += 1
            if count > loop_limit:
                break

        return result if result else [text]

    @staticmethod
    def split_teachers(row: pd.Series, teacher_set: Set[str], max_name_len: int) -> List[str]:
        """教師姓名處理入口"""
        teacher_str = row.get('教師姓名')
        if pd.isna(teacher_str):
            return []

        text = str(teacher_str).strip()
        if text in ['校際教師', '校外教師']:
            return ['校際教']
        return DataProcessor.split_teachers_by_dict(text, teacher_set, max_name_len)

    def clean_single_file(self, csv_file: Path, teacher_set: Set[str], max_name_len: int) -> Optional[pd.DataFrame]:
        """清理單一檔案"""
        df = safe_read_csv(csv_file)
        if df is None:
            return None

        year, semester = extract_year_semester_from_filename(csv_file)
        if year is None:
            return None

        df['學年度'] = year
        df['學期'] = semester

        df['parsed_schedule'] = df['上課節次+地點'].apply(self.parse_schedule_location)
        df = df.explode('parsed_schedule')
        df.reset_index(drop=True, inplace=True)

        schedule_df = pd.json_normalize(df['parsed_schedule'])
        df = pd.concat([df.drop(columns=['parsed_schedule']), schedule_df], axis=1)

        df['教師列表'] = df.apply(lambda row: self.split_teachers(row, teacher_set, max_name_len), axis=1)
        df['教師列表'] = df['教師列表'].apply(lambda x: ", ".join(x) if isinstance(x, list) else str(x))

        df = self.department_mapper.add_department_info_to_df(df)
        def determine_system(class_name):
            s = str(class_name)
            if '夜' in s:
                return '夜間部'
            return '日間部'

        def determine_level(class_name):
            s = str(class_name)
            if '博' in s:
                return '博士班'
            if '碩' in s:
                return '碩士班'
            return '大學部'

        # 確保使用原始的 '開課班別(代表)' 欄位進行判斷
        if '開課班別(代表)' in df.columns:
            df['學制'] = df['開課班別(代表)'].apply(determine_system)
            df['部別'] = df['開課班別(代表)'].apply(determine_level)
        else:
            df['學制'] = '日間部'
            df['部別'] = '大學部'

        if '星期' in df.columns:
            df['星期'] = df['星期'].astype(str).str.replace(r'[()]', '', regex=True).replace('nan', '', regex=False)

        text_cols_to_fill = ['備註', '英文課程名稱', '教師姓名', '上課地點', '上課大樓', '上課節次+地點']
        for col in text_cols_to_fill:
            if col in df.columns:
                df[col] = df[col].fillna("")

        count_cols = ['上限人數', '登記人數', '選上人數']
        for col in count_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

        if '全英語授課' in df.columns:
            df['全英語授課'] = df['全英語授課'].map({'是': True, '否': False}).fillna(False)

        cols_to_keep = [
            '學年度', '學期', '序號', '課程代碼', '開課班別(代表)',
            '學院', '科系', '年級', '班級',
            '學制', '部別',
            '教學大綱Syllabus', '教學大綱連結', '教學大綱狀態',
            '課程名稱', '英文課程名稱',
            '課程性質', '課程性質2', '全英語授課', '學分',
            '教師姓名', '教師列表',
            '上課大樓', '上課節次+地點',
            '星期', '起始節次', '結束節次', '上課地點',
            '上限人數', '登記人數', '選上人數',
            '可跨班', '備註'
        ]
        final_cols = [c for c in cols_to_keep if c in df.columns]
        return df[final_cols]

    def build_all_courses_dataset(self, input_dir: Path, teacher_dict_path: Path) -> pd.DataFrame:
        csv_files = sorted(input_dir.glob("courses_*.csv"))

        if not csv_files:
            raise RuntimeError(f"{input_dir} 內找不到 courses_*.csv")

        self.logger.info(f"正在載入教師字典：{teacher_dict_path}")
        teacher_set, max_name_len = self.load_teacher_set(teacher_dict_path)
        
        dfs = []
        for csv_file in csv_files:
            self.logger.info(f"處理中：{csv_file.name}")
            df_clean = self.clean_single_file(csv_file, teacher_set, max_name_len)
            if df_clean is not None:
                dfs.append(df_clean)

        if not dfs:
            return pd.DataFrame()

        all_df = pd.concat(dfs, ignore_index=True)
        self.logger.info(f"合併完成，共 {len(all_df)} 筆資料")
        return all_df

def main():
    from utils.common import setup_logging
    setup_logging()

    processor = DataProcessor()
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        final_df = processor.build_all_courses_dataset(RAW_DATA_DIR, TEACHER_DICT_PATH)
        if not final_df.empty:
            timestamp = get_timestamp()
            output_path = PROCESSED_DATA_DIR / f"all_courses_{timestamp}.csv"
            safe_write_csv(final_df, output_path)
            print(f"\n成功！最終檔案已儲存：{output_path}")
    except Exception as e:
        logging.error(f"處理失敗: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()