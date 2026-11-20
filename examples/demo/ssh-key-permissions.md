# SSH 私钥权限过宽

## 症状

``` 
WARNING: UNPROTECTED PRIVATE KEY FILE!
Permissions 0644 for '/home/me/.ssh/id_ed25519' are too open.
This private key will be ignored.
```

昨天还能连，今天突然连不上；或者只有某个用户连不上。

## 原因

OpenSSH 7.0 之后强制检查私钥与 `.ssh` 目录的权限，属主必须是当前用户、组和其他人不能有任何权限。常见触发：

- 从 Windows 拷过来时权限变成 0644；
- `umask` 宽松，`ssh-keygen` 生成的私钥带 group 读；
- 整个目录被 `chmod -R` 或同步工具改过；
- 权限对了但**属主**是 root（用 sudo 拷过文件）。

## 做法

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
chmod 644 ~/.ssh/id_ed25519.pub ~/.ssh/known_hosts ~/.ssh/config
chown -R "$USER":"$USER" ~/.ssh
```

`~/.ssh/config` 是 600 也可以，但绝不能可写于组。

## 验证

`ls -l ~/.ssh` 里私钥是 `-rw-------`，目录是 `drwx------`；再 `ssh -vT git@github.com`，输出里不再出现 `UNPROTECTED PRIVATE KEY FILE`。

不想被这个检查打扰时（例如只读的容器镜像里），可以 `ssh -o StrictModes=no`，但那是绕过而不是修复——服务器端的 `StrictModes` 仍然生效。
