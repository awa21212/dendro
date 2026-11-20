# 慢查询有索引却全表扫

## 症状

`EXPLAIN ANALYZE` 显示 `Seq Scan`，但 `\d table` 里索引明明在，`WHERE` 的列也建了索引。

## 三个最常见的原因

1. **类型不匹配**：列是 `varchar`，条件写成 `WHERE id = 123`（整数）。Postgres 会做隐式转换，索引失效。改成 `WHERE id = '123'`。
2. **函数包住了列**：`WHERE lower(email) = 'a@b.c'` 用不到普通索引，要建 `CREATE INDEX ON t (lower(email))`。
3. **统计信息过期**，或者匹配行数占比太高（>5%~10%）时，优化器故意选顺序扫描——这种情况下全表扫是对的。

## 定位顺序

```sql
-- 1. 看真实的计划与实际行数
EXPLAIN (ANALYZE, BUFFERS) SELECT ...;

-- 2. 看统计信息是不是旧的
SELECT last_analyze, last_autoanalyze FROM pg_stat_user_tables WHERE relname = 't';
ANALYZE t;

-- 3. 强制关掉顺序扫描，确认索引本身可用
SET enable_seqscan = off;
EXPLAIN (ANALYZE) SELECT ...;
```

## 验证

`enable_seqscan=off` 之后如果变成 `Index Scan` 且耗时更低，说明是代价估算问题（常见于统计信息过期）；如果还是慢，说明索引选择性本身不够。
