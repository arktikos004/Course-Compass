"""檢查 processed CSV 的欄位結構與樣本值"""

import pandas as pd
import os
from pathlib import Path

PROCESSED_DIR = Path('data/processed')
files = sorted(PROCESSED_DIR.glob('all_courses_*.csv'))
if not files:
    print('No processed files found in', PROCESSED_DIR)
    raise SystemExit(1)
latest = files[-1]
print('Checking:', latest)
df = pd.read_csv(latest)
required = ['課程名稱','星期','起始節次','結束節次','上課地點','學分','課程代碼','序號']
missing = [c for c in required if c not in df.columns]
if missing:
    print('Missing columns:', missing)
else:
    print('All required columns present')

for c in required:
    non_null = df[c].notna().sum()
    total = len(df)
    print(f"{c}: {non_null}/{total} non-null")

for c in ['起始節次','結束節次','學分']:
    if c in df.columns:
        def is_number(val):
            try:
                if pd.isna(val) or str(val).strip()=='':
                    return False
                float(val)
                return True
            except Exception:
                return False
        invalid = df[~df[c].apply(is_number)]
        print(f"{c} sample invalid count: {len(invalid)}")
        if len(invalid) > 0:
            print(invalid[[c]].head(5).to_string(index=False))

print('\nSample rows:')
print(df[required].head(10).to_string(index=False))
