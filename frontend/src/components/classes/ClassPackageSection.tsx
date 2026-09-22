/**
 * 班级数据包区块(班级管理页内;自班级分析页迁移)
 *
 * - 导出当前班级的完整数据包(ZIP):任务 / 错因 / 花名册 / 台账 / 考试,可拷贝到其他电脑导入;
 * - 导入班级数据包(重名自动改名),导入完成后刷新班级列表。
 */

import { useRef, useState } from 'react'
import { Alert, Box, Button, CircularProgress, Stack, TextField, Tooltip } from '@mui/material'
import DownloadOutlinedIcon from '@mui/icons-material/DownloadOutlined'
import UploadFileOutlinedIcon from '@mui/icons-material/UploadFileOutlined'
import {
  downloadBlob,
  exportClassPackageBlob,
  extractBlobError,
  importClassPackage,
} from '../../api/client'
import type { SchoolClass } from '../../types'

interface Props {
  /** 班级列表(用于导出文件名与展示当前班级名称) */
  classes: SchoolClass[]
  /** 当前班级('' = 未选择,导出按钮禁用) */
  classId: number | ''
  /** 导入完成后的回调(刷新班级列表) */
  onImported: () => void
}

export default function ClassPackageSection({ classes, classId, onImported }: Props) {
  const [exporting, setExporting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [message, setMessage] = useState<{ severity: 'success' | 'error'; text: string } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const current = classes.find((c) => c.id === classId)
  const currentLabel = classId === '' ? '未选择(请在上方选择班级后导出)' : (current?.name ?? `#${classId}`)

  /** 导出当前筛选班级的数据包 */
  const handleExport = async () => {
    if (classId === '') return
    setMessage(null)
    setExporting(true)
    try {
      const blob = await exportClassPackageBlob(classId)
      downloadBlob(blob, `班级数据包_${current?.name ?? classId}_${new Date().toISOString().slice(0, 10)}.zip`)
      setMessage({ severity: 'success', text: '班级数据包已导出,可拷贝到其他电脑导入使用' })
    } catch (e) {
      setMessage({ severity: 'error', text: await extractBlobError(e) })
    } finally {
      setExporting(false)
    }
  }

  /** 导入班级数据包 */
  const handleImportFile = async (file: File) => {
    setImporting(true)
    setMessage(null)
    try {
      const result = await importClassPackage(file)
      setMessage({
        severity: 'success',
        text: `导入完成:班级「${result.class_name}」${result.renamed ? '(因重名已自动改名)' : ''},共 ${result.task_count} 个批改任务 / ${result.error_count} 条错因 / ${result.image_count} 张图片`,
      })
      onImported()
    } catch (e) {
      setMessage({ severity: 'error', text: await extractBlobError(e) })
    } finally {
      setImporting(false)
    }
  }

  return (
    <Box sx={{ maxWidth: 720 }}>
      <Alert severity="info" sx={{ mb: 2 }}>
        数据包包含:批改任务 / 错因记录 / 花名册 / 台账 / 考试(含图片)。用于跨电脑迁移班级数据;
        导入时重名班级会自动改名,不会覆盖现有数据。
      </Alert>
      <Stack spacing={2}>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
          <Tooltip title={classId === '' ? '请先在上方选择班级' : '把该班全部数据打包导出,可在其他电脑导入'}>
            <span>
              <Button
                variant="outlined"
                startIcon={exporting ? <CircularProgress size={14} /> : <DownloadOutlinedIcon />}
                disabled={classId === '' || exporting}
                onClick={() => void handleExport()}
              >
                {exporting ? '正在导出…' : '导出当前班级数据包'}
              </Button>
            </span>
          </Tooltip>
          <Button
            variant="contained"
            startIcon={importing ? <CircularProgress size={14} color="inherit" /> : <UploadFileOutlinedIcon />}
            disabled={importing}
            onClick={() => fileInputRef.current?.click()}
          >
            {importing ? '正在导入…' : '导入班级数据包'}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            accept=".zip"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void handleImportFile(f)
              e.target.value = ''
            }}
          />
        </Stack>
        <TextField
          size="small"
          label="当前班级"
          value={currentLabel}
          disabled
          fullWidth
        />
        {message && (
          <Alert severity={message.severity} onClose={() => setMessage(null)}>
            {message.text}
          </Alert>
        )}
      </Stack>
    </Box>
  )
}
