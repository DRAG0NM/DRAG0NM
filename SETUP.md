# 设置

仓库名必须与用户名相同（`DRAG0NM/DRAG0NM`），README 才会显示在个人主页上。推送到 GitHub 后，到 Actions 页手动运行一次 “Update profile charts” 即可生成图片；之后每天自动更新。

## 可选：统计私有仓库

默认只统计公开仓库。想把私有仓库也计入语言占比，需要自己做三步：

1. 创建 fine-grained personal access token：Resource owner 选自己，Repository access 选 All repositories，权限只给只读的 Metadata 和 Contents。
2. 在本仓库 Settings → Secrets and variables → Actions → Secrets 中新增 `PROFILE_TOKEN`，值为上面的 token。
3. 在同一页的 Variables 中新增 `INCLUDE_PRIVATE`，值为 `1`。

说明：私有仓库只进入语言占比（和贡献日历），名字不会写进任何图片；星标图永远只画公开仓库。想改回只统计公开仓库，把 `INCLUDE_PRIVATE` 删掉或设为 `0` 即可。
