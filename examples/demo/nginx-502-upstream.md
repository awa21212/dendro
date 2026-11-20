# Nginx 502 上游超时

## 症状

间歇性 502，`error.log` 里是：

```
upstream prematurely closed connection while reading response header from upstream
```

接口本身在 Postman 里直连没问题，经过 Nginx 才挂。

## 排查顺序

1. **上游到底超时没有**：`/etc/nginx/nginx.conf` 里 `proxy_read_timeout` 默认 60s；上游慢查询 65s 就被切。看 `upstream_response_time` 是否接近 60。
2. **缓冲区**：`proxy_buffering on` 时上游一次性吐大响应，Nginx 先落盘再转发；磁盘满或 `client_body_buffer_size` 太小时会异常断开。
3. **keepalive 复用**：上游是 Python 的话，`Connection: close` 处理不干净会留下半死连接，下一个请求复用就 502。开 `proxy_next_upstream error timeout http_502;` 只是遮住症状。
4. **后端进程数 /max requests**：`gunicorn --max-requests` 到点自杀，正好撞上请求会看到同样的日志。

## 定位

```bash
# 看是不是固定 60s 整
tail -f /var/log/nginx/error.log | grep -E 'upstream|timed out'
# 看上游是否被重启
journalctl -u gunicorn --since '1 hour ago' | grep -i 'booting\|worker'
```

## 验证

把 `proxy_read_timeout` 调到 120s、`proxy_http_version 1.1` + `proxy_set_header Connection ""` 打开 keepalive 之后，如果 502 消失但接口仍然慢，说明真实问题是上游性能，Nginx 只是先报出来了。
