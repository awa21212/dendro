# 改几行却整个文件重写

## 症状

只改了三行代码，`git diff --stat` 却报几百行改动；`git blame` 之后整片代码的作者都变成自己，同事 review 时完全看不出真正的修改点。

## 原因

**行尾混用**。仓库里原本是 CRLF（Windows 检出），你的编辑器存成了 LF（或反过来），于是每一行都算"变了"。`core.autocrlf` 的取值会让这个现象时有时无：

| `core.autocrlf` | 提交时 | 检出时 |
|---|---|---|
| `true`（Windows 常见） | CRLF → LF | LF → CRLF |
| `input`（Linux/macOS 常见） | CRLF → LF | 不动 |
| `false` | 原样 | 原样 |

团队里有人 `true` 有人 `input`，同一个文件就会来回翻转。

## 做法

1. 仓库根目录放 `.gitattributes`：

   ```
   * text=auto eol=lf
   *.bat text eol=crlf
   *.cmd text eol=crlf
   *.ps1 text eol=crlf
   ```

2. 已提交的历史文件先归一化：`git add --renormalize .` 单独一个提交，提交信息写「normalize line endings」，与逻辑改动**分开**。
3. 检查暂存区里到底是什么：`git diff --cached --stat`（工作区干净不代表暂存区干净）。

## 验证

`git diff --stat` 的行数应当接近真实改动；`file your.cpp` 显示 `with CRLF line terminators` 或 `ASCII text` 其一，而不是混着来。
