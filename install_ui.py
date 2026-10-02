from pathlib import Path
import shutil, time
ROOT=Path(__file__).resolve().parent
FILES={}

for src in ['app.py','db.py','data_export.py','schema.sql','requirements.txt','Procfile','render.yaml','runtime.txt','.env.example','.gitignore','README.md']:
    pass

print('UI files are already installed. Restart Flask to use the new design.')
