/**
 * 全局健康状态 Hook
 *
 * 拉取 /api/health 并缓存于模块级,供多处(外壳提示、设置抽屉、
 * 工作台的演示模式提示)共享;支持手动刷新。
 */

import { useCallback, useEffect, useState } from 'react'
import { fetchHealth } from '../api/client'
import type { HealthResponse } from '../types'

/** 模块级缓存:避免多组件重复请求 */
let cachedHealth: HealthResponse | null = null
const subscribers = new Set<(h: HealthResponse | null) => void>()

/** 通知所有订阅者 */
function publish(health: HealthResponse | null) {
  cachedHealth = health
  subscribers.forEach((fn) => fn(health))
}

/** 使用全局健康状态(自动刷新一次) */
export function useHealth() {
  const [health, setHealth] = useState<HealthResponse | null>(cachedHealth)

  useEffect(() => {
    subscribers.add(setHealth)
    // 首次挂载时拉取
    if (cachedHealth === null) {
      fetchHealth().then(publish).catch(() => publish(null))
    }
    return () => {
      subscribers.delete(setHealth)
    }
  }, [])

  return health
}

/** 手动刷新健康状态(设置抽屉"刷新"按钮用) */
export function useRefreshHealth() {
  const [loading, setLoading] = useState(false)
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const h = await fetchHealth()
      publish(h)
    } catch {
      publish(null)
    } finally {
      setLoading(false)
    }
  }, [])
  return { refresh, loading }
}
