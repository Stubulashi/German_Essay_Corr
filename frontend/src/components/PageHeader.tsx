/**
 * 页面标题区(全站统一规范,详见 docs/UI规范.md)
 *
 * 统一各页面的标题层级与间距:
 * - 标题:h4(主题统一字重与字距);
 * - 徽章:与标题同行展示的小标签(如数据口径说明、任务计数);
 * - 说明:body2 次要色,限制最大宽度保证可读性;
 * - 操作:右侧对齐,小屏自动换行堆叠。
 *
 * 使用约定:
 * - 页面级标题一律使用本组件,避免各页自写 h4/h5 造成层级不一致;
 * - 操作区按钮统一 size="small" + color="inherit"(主操作除外)。
 */

import type { ReactNode } from 'react'
import { Box, Stack, Typography } from '@mui/material'
import { useUiProfile } from '../theme'

interface Props {
  /** 页面标题 */
  title: string
  /** 标题下方的说明文字(一句话讲清"这个页面能做什么") */
  subtitle?: ReactNode
  /** 标题同行的小徽章(Chip 等) */
  chips?: ReactNode
  /** 右侧操作区(按钮组 / 筛选项等) */
  actions?: ReactNode
}

export default function PageHeader({ title, subtitle, chips, actions }: Props) {
  // premium 档:标题下渲染渐变细线(尊享氛围;其余档位不渲染)
  const { profile } = useUiProfile()
  return (
    <Box
      sx={{
        mb: 3,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1.5,
        alignItems: 'flex-start',
      }}
    >
      <Box sx={{ flex: '1 1 320px', minWidth: 0 }}>
        <Stack direction="row" spacing={1.2} alignItems="center" sx={{ flexWrap: 'wrap' }}>
          <Typography variant="h4">{title}</Typography>
          {chips}
        </Stack>
        {profile === 'premium' && (
          <Box
            aria-hidden
            sx={{
              mt: 1.1,
              width: 72,
              height: 3,
              borderRadius: 999,
              background: (theme) =>
                theme.palette.mode === 'light'
                  ? 'linear-gradient(90deg, rgba(63,81,181,0.75), rgba(63,81,181,0.06))'
                  : 'linear-gradient(90deg, rgba(121,134,203,0.80), rgba(121,134,203,0.08))',
            }}
          />
        )}
        {subtitle && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mt: 0.8, maxWidth: 760 }}
          >
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions && (
        <Stack
          direction="row"
          spacing={1.2}
          alignItems="center"
          sx={{ flexWrap: 'wrap', rowGap: 1 }}
        >
          {actions}
        </Stack>
      )}
    </Box>
  )
}
