# GitHub 操作手册

本项目目录：

```powershell
cd D:\Code\adb
```

## 1. 查看当前状态

每次操作 Git 前，先看当前分支和本地改动：

```powershell
git status --short --branch
```

常见结果：

- `## main...origin/main`：当前在 `main` 分支。
- `M xxx.py`：文件有本地修改，还没有提交。
- `?? xxx.md`：新文件，还没有加入 Git。
- `Your branch is up to date`：本地代码和 GitHub 一致。

查看最近提交：

```powershell
git log --oneline -5
```

## 2. 从 GitHub 更新代码

如果本地没有未提交改动，直接执行：

```powershell
git fetch origin main
git merge --ff-only origin/main
```

也可以用：

```powershell
git pull --ff-only origin main
```

推荐第一种，因为 `fetch` 后可以先看情况再合并。

## 3. 本地有改动时更新 GitHub 代码

如果 `git status` 看到有 `M` 或 `??`，先不要直接 `pull`。

推荐流程：

```powershell
git status --short --branch
git stash push -m "backup-before-github-update"
git fetch origin main
git merge --ff-only origin/main
git stash pop
```

说明：

- `git stash push`：临时保存本地改动。
- `git merge --ff-only origin/main`：更新到 GitHub 最新代码。
- `git stash pop`：把刚才保存的本地改动恢复回来。

如果 `git stash pop` 后提示冲突，需要手动处理冲突文件。

## 4. 提交本地代码

先查看改了哪些文件：

```powershell
git status --short
git diff --stat
```

查看具体改动：

```powershell
git diff
```

加入要提交的文件：

```powershell
git add 路径\文件名
```

比如：

```powershell
git add apps\finance_crawler\services\report.py
git add docs\GITHUB_OPERATIONS.md
```

如果确认所有改动都要提交：

```powershell
git add .
```

提交：

```powershell
git commit -m "Update report workflow"
```

提交信息建议用英文短句，说明做了什么。

## 5. 上传代码到 GitHub

提交后推送：

```powershell
git push origin main
```

推送完成后，可以确认状态：

```powershell
git status --short --branch
```

如果显示没有改动，并且分支没有 ahead，说明本地和 GitHub 已同步。

## 6. 常见完整流程

### 6.1 只更新 GitHub 最新代码

```powershell
cd D:\Code\adb
git status --short --branch
git fetch origin main
git merge --ff-only origin/main
```

### 6.2 修改后提交并上传

```powershell
cd D:\Code\adb
git status --short --branch
git diff --stat
git add .
git commit -m "Describe your change"
git push origin main
```

### 6.3 有本地改动，又要先更新 GitHub

```powershell
cd D:\Code\adb
git status --short --branch
git stash push -m "backup-before-update"
git fetch origin main
git merge --ff-only origin/main
git stash pop
git status --short --branch
```

如果恢复后确认没问题，再提交：

```powershell
git add .
git commit -m "Describe your change"
git push origin main
```

## 7. 冲突处理

如果看到类似：

```text
CONFLICT (content): Merge conflict in xxx.py
```

先查看冲突文件：

```powershell
git status --short
```

打开文件，搜索：

```text
<<<<<<<
=======
>>>>>>>
```

手动保留正确内容，并删除这些冲突标记。

处理完后：

```powershell
git add 冲突文件路径
git commit -m "Resolve merge conflict"
```

如果冲突来自 `git stash pop`，处理完后正常提交即可。

## 8. 放弃本地改动

谨慎使用。确认某个文件的本地改动不要了：

```powershell
git restore 路径\文件名
```

删除未跟踪的新文件：

```powershell
git clean -n
git clean -f
```

说明：

- `git clean -n`：只预览会删除哪些文件。
- `git clean -f`：真正删除未跟踪文件。

不要随便执行：

```powershell
git reset --hard
```

它会丢弃所有本地未提交改动。

## 9. 查看远端地址

```powershell
git remote -v
```

本项目远端一般是：

```text
origin  https://github.com/easyLong/adb.git
```

## 10. 更新代码后重启项目

如果 workers 正在跑，更新代码不会自动影响已经启动的 Python 进程。

更新代码后需要重启 workers：

```powershell
.\scripts\run.ps1 -Task workers-stop
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

如果当前正在跑重要任务，先确认是否可以中断，再重启。
