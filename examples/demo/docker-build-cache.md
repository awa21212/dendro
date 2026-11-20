# 改了 Dockerfile 却不重建层

## 症状

改了一行依赖，`docker build` 输出里还是一堆 `CACHED`，镜像里跑起来仍是旧代码。

## 原因

`COPY . .` 放在 `RUN pip install -r requirements.txt` **前面**。只要任何文件变了，这一层的哈希就变，后面所有层跟着失效——看似"缓存命中"，其实是因为构建上下文里有个被 `.dockerignore` 漏掉的目录在抖动，例如 `node_modules/`、`.git/`、日志目录。

## 做法

1. 先复制依赖清单，装完依赖再复制源码：

   ```dockerfile
   COPY requirements.txt .
   RUN pip install -r requirements.txt
   COPY . .
   ```

2. `.dockerignore` 至少写：`.git`、`__pycache__`、`*.log`、`node_modules`、`.venv`。
3. 想看某一层到底为什么失效，用 `docker build --progress=plain --no-cache-filter=builder .`，输出里会打印每一层的输入哈希。

## 验证

连跑两次 `docker build`，第二次除最后 `COPY . .` 外应当是 `CACHED`；改一个 `.py` 文件再跑，只有最后一层失效。
