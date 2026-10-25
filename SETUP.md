# 设置

仓库名必须与用户名相同（`DRAG0NM/DRAG0NM`），README 才会显示在个人主页上。推送到 GitHub 后，到 Actions 页手动运行一次 “Update profile charts” 即可生成图片；之后每天自动更新。

## 可选：统计私有仓库

默认只统计公开仓库。想把私有仓库也计入语言占比，需要自己做三步：

1. 创建 classic personal access token：Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)，勾选 `repo` 和 `read:user`，有效期可选 90 天。fine-grained token 读不了 star 列表，所以不要用。
2. 在本仓库 Settings → Secrets and variables → Actions → Secrets 中新增 `PROFILE_TOKEN`，值为上面的 token。
3. 在同一页的 Variables 中新增 `INCLUDE_PRIVATE`，值为 `1`。

token 过期后 Action 会失败，续期后在 Secret 里覆盖 `PROFILE_TOKEN` 即可。

说明：私有仓库只进入语言占比（和贡献日历），名字不会写进任何图片；星标图永远只画公开仓库。想改回只统计公开仓库，把 `INCLUDE_PRIVATE` 删掉或设为 `0` 即可。

星迹图（star history）还需要 `PROFILE_TOKEN`。GitHub 现在要求登录才能读取 stargazers 列表，Actions 自带的 token 读不到，所以不配置 `PROFILE_TOKEN` 时，星迹图会一直保留上一次生成的版本，不会自动更新。配置之后，星迹图每天自动更新。同一个 token 即可，不需要额外设置。
