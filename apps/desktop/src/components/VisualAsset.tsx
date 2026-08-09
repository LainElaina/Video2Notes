import type { ImgHTMLAttributes } from 'react'
import { visualAssets, type VisualAssetKey } from '../assets/visualAssets'

interface VisualAssetProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> {
  asset: VisualAssetKey
  width?: number
  height?: number
}

export function VisualAsset({
  asset,
  width = 640,
  height = 640,
  alt = '',
  loading = 'lazy',
  decoding = 'async',
  ...props
}: VisualAssetProps) {
  const decorative = alt === ''
  const className = ['visual-asset', props.className].filter(Boolean).join(' ')
  return (
    <img
      {...props}
      className={className}
      src={visualAssets[asset]}
      alt={alt}
      width={width}
      height={height}
      loading={loading}
      decoding={decoding}
      aria-hidden={decorative ? true : undefined}
      draggable={false}
    />
  )
}
