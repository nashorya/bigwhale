import ast
import sys

files = [
    'plugins/shore/core/csv_export.py',
    'plugins/shore/handlers/weekly_plan.py',
    'plugins/shore/core/ai_service.py',
    'plugins/shore/core/scheduler.py',
    'plugins/shore/core/user_db.py',
]
ok = True
for f in files:
    try:
        with open(f, encoding='utf-8') as fp:
            ast.parse(fp.read())
        print(f'OK: {f}')
    except SyntaxError as e:
        print(f'SYNTAX ERROR in {f}: {e}')
        ok = False
    except Exception as e:
        print(f'ERROR reading {f}: {e}')
        ok = False

sys.exit(0 if ok else 1)
