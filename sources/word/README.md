# 原始 Word 讲义

此目录用于本地保留迁移前的 DOCX 原稿。`*.docx` 已被 `.gitignore` 排除，不会发布到
GitHub。

如需重新迁移：

```powershell
python -m pip install -r requirements-migration.txt
python scripts/docx_to_markdown.py sources/word --clean
```

迁移会重写 `docs/chapters/` 和 `docs/assets/`，执行后必须审查 Git Diff，并运行：

```powershell
python scripts/check_docs.py
python -m unittest discover -s tests -v
```
