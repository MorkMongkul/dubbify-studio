// src/features/upload/UploadDropzone.tsx
import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Upload, Film, FileText, X, CheckCircle,
  CloudUpload, Loader2, AlertCircle
} from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { useUploadVideo, useUploadWithSubtitle } from '@/hooks/useApi'

interface UploadDropzoneProps {
  projectId: string
  onSuccess?: (jobId: string) => void
}

export function UploadDropzone({ projectId, onSuccess }: UploadDropzoneProps) {
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [subtitleFile, setSubtitleFile] = useState<File | null>(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)

  const { mutate: uploadVideo, isPending: uploadingVideo } = useUploadVideo()
  const { mutate: uploadWithSub, isPending: uploadingWithSub } = useUploadWithSubtitle()
  const isUploading = uploadingVideo || uploadingWithSub

  const onDropVideo = useCallback((files: File[]) => {
    const file = files[0]
    if (!file) return
    const allowed = ['video/mp4', 'video/mkv', 'video/webm', 'video/avi', 'video/mov', 'video/quicktime', 'video/x-matroska']
    if (!allowed.includes(file.type) && !file.name.match(/\.(mp4|mkv|webm|avi|mov)$/i)) {
      toast.error('Please upload a video file (MP4, MKV, WebM, AVI, MOV)')
      return
    }
    setVideoFile(file)
    setUploadError(null)  // clear any previous error
    setUploadProgress(0)
  }, [])

  const onDropSubtitle = useCallback((files: File[]) => {
    const file = files[0]
    if (!file) return
    if (!file.name.match(/\.(srt|vtt|ass|ssa|sub)$/i)) {
      toast.error('Please upload a subtitle file (SRT, VTT, ASS)')
      return
    }
    setSubtitleFile(file)
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: onDropVideo,
    accept: {
      'video/*': ['.mp4', '.mkv', '.webm', '.avi', '.mov'],
      'video/x-matroska': ['.mkv'],
    },
    maxFiles: 1,
    disabled: isUploading,
    noClick: !!videoFile,
  })

  const { getRootProps: getSubRootProps, getInputProps: getSubInputProps, isDragActive: isSubDragActive } = useDropzone({
    onDrop: onDropSubtitle,
    accept: { 'text/*': ['.srt', '.vtt', '.ass', '.ssa', '.sub'] },
    maxFiles: 1,
    disabled: isUploading || !videoFile,
  })

  const getErrorMessage = (err: unknown): string => {
    const axiosErr = err as { message?: string; response?: { status?: number; statusText?: string } }
    const status = axiosErr?.response?.status
    const msg = axiosErr?.message || 'Upload failed'
    return status ? `${status} · ${msg}` : msg
  }

  const handleUpload = () => {
    if (!videoFile) {
      toast.error('Please select a video file first')
      return
    }

    setUploadError(null)

    // Simulate progress
    const interval = setInterval(() => {
      setUploadProgress((p) => {
        if (p >= 90) { clearInterval(interval); return p }
        return p + Math.random() * 8
      })
    }, 300)

    if (subtitleFile) {
      uploadWithSub(
        { projectId, video: videoFile, subtitle: subtitleFile },
        {
          onSuccess: (data) => {
            clearInterval(interval)
            setUploadProgress(100)
            toast.success('Upload complete! Pipeline started.')
            onSuccess?.(data.job_id)
          },
          onError: (err) => {
            clearInterval(interval)
            setUploadProgress(0)
            const msg = getErrorMessage(err)
            setUploadError(msg)
            toast.error(msg, { duration: 6000 })
          },
        }
      )
    } else {
      uploadVideo(
        { projectId, file: videoFile },
        {
          onSuccess: (data) => {
            clearInterval(interval)
            setUploadProgress(100)
            toast.success('Upload complete! Pipeline started.')
            onSuccess?.(data.job_id)
          },
          onError: (err) => {
            clearInterval(interval)
            setUploadProgress(0)
            const msg = getErrorMessage(err)
            setUploadError(msg)
            toast.error(msg, { duration: 6000 })
          },
        }
      )
    }
  }

  const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  return (
    <div className="space-y-4">
      {/* Main video drop zone */}
      <div
        {...getRootProps()}
        className={cn(
          'relative rounded-2xl border-2 border-dashed transition-all duration-200 cursor-pointer overflow-hidden',
          isDragActive
            ? 'border-brand bg-brand/8 shadow-glow'
            : videoFile
            ? 'border-emerald-500/40 bg-emerald-500/5'
            : 'border-border hover:border-brand/50 hover:bg-brand/4 bg-surface-3',
          isUploading && 'pointer-events-none'
        )}
      >
        <input {...getInputProps()} />

        <AnimatePresence mode="wait">
          {videoFile ? (
            <motion.div
              key="file-preview"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="flex items-center gap-4 p-5"
            >
              <div className="h-12 w-12 rounded-xl bg-emerald-500/15 border border-emerald-500/25 flex items-center justify-center shrink-0">
                <Film size={20} className="text-emerald-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-medium text-text-primary text-sm truncate">{videoFile.name}</p>
                <p className="text-xs text-text-muted mt-0.5">{formatBytes(videoFile.size)}</p>
              </div>
              <div className="flex items-center gap-2">
                <CheckCircle size={16} className="text-emerald-400" />
                {!isUploading && (
                  <button
                    className="p-1 rounded-md hover:bg-white/10 text-text-muted hover:text-text-primary transition-colors"
                    onClick={(e) => { e.stopPropagation(); setVideoFile(null); setUploadProgress(0) }}
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex flex-col items-center justify-center py-14 px-6 text-center"
            >
              <motion.div
                className={cn(
                  'h-16 w-16 rounded-2xl flex items-center justify-center mb-4',
                  isDragActive ? 'bg-brand/25 border-2 border-brand/50' : 'bg-surface-4 border border-border'
                )}
                animate={isDragActive ? { scale: [1, 1.1, 1] } : {}}
                transition={{ duration: 0.4 }}
              >
                <CloudUpload size={28} className={isDragActive ? 'text-brand-400' : 'text-text-muted'} />
              </motion.div>

              {isDragActive ? (
                <div>
                  <p className="font-semibold text-brand-300 text-base">Drop your video here</p>
                  <p className="text-sm text-text-muted mt-1">Release to upload</p>
                </div>
              ) : (
                <div>
                  <p className="font-semibold text-text-primary text-base">Drop your video here</p>
                  <p className="text-sm text-text-muted mt-1.5">or click to browse</p>
                  <p className="text-xs text-text-disabled mt-3">MP4, MKV, WebM, AVI, MOV supported</p>
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Upload progress bar */}
        {isUploading && uploadProgress > 0 && (
          <div className="absolute bottom-0 left-0 right-0 h-1 bg-surface-4">
            <motion.div
              className="h-full bg-gradient-to-r from-brand to-accent rounded-full"
              initial={{ width: 0 }}
              animate={{ width: `${uploadProgress}%` }}
              transition={{ duration: 0.3 }}
            />
          </div>
        )}
      </div>

      {/* Optional subtitle drop zone */}
      {videoFile && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2 }}
        >
          <div
            {...getSubRootProps()}
            className={cn(
              'rounded-xl border border-dashed transition-all duration-200 cursor-pointer',
              isSubDragActive
                ? 'border-accent bg-accent/8'
                : subtitleFile
                ? 'border-blue-500/40 bg-blue-500/5'
                : 'border-border hover:border-border-strong hover:bg-white/3 bg-surface-3',
            )}
          >
            <input {...getSubInputProps()} />
            <div className="flex items-center gap-3 px-4 py-3">
              <div className="h-8 w-8 rounded-lg bg-surface-4 border border-border flex items-center justify-center shrink-0">
                <FileText size={14} className={subtitleFile ? 'text-blue-400' : 'text-text-muted'} />
              </div>
              <div className="flex-1 min-w-0">
                {subtitleFile ? (
                  <>
                    <p className="text-sm font-medium text-text-primary truncate">{subtitleFile.name}</p>
                    <p className="text-xs text-text-muted">{formatBytes(subtitleFile.size)}</p>
                  </>
                ) : (
                  <>
                    <p className="text-sm text-text-secondary">
                      <span className="text-accent font-medium">Optional:</span> Drop subtitle file
                    </p>
                    <p className="text-xs text-text-disabled">SRT, VTT, ASS — skips AI transcription</p>
                  </>
                )}
              </div>
              {subtitleFile && (
                <button
                  className="p-1 rounded-md hover:bg-white/10 text-text-muted hover:text-text-primary transition-colors"
                  onClick={(e) => { e.stopPropagation(); setSubtitleFile(null) }}
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>
        </motion.div>
      )}

      {/* Upload button */}
      <AnimatePresence>
        {videoFile && !isUploading && uploadProgress === 0 && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
          >
            <Button
              variant="default"
              size="lg"
              className="w-full"
              onClick={handleUpload}
              icon={<Upload size={16} />}
            >
              Start AI Pipeline
            </Button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Inline error banner */}
      <AnimatePresence>
        {uploadError && !isUploading && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15 }}
            className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg border text-[12px]"
            style={{
              background: 'rgba(239,68,68,0.07)',
              borderColor: 'rgba(239,68,68,0.22)',
              color: '#F87171',
            }}
          >
            <AlertCircle size={13} className="shrink-0 mt-px" />
            <span className="flex-1 leading-snug">{uploadError}</span>
            <button
              className="shrink-0 opacity-60 hover:opacity-100 transition-opacity"
              onClick={() => setUploadError(null)}
            >
              <X size={12} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {isUploading && (
        <div className="flex items-center justify-center gap-3 py-2">
          <Loader2 size={16} className="animate-spin text-brand-400" />
          <span className="text-sm text-text-secondary">
            Uploading… {Math.round(uploadProgress)}%
          </span>
        </div>
      )}
    </div>
  )
}
