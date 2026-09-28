import { describe, expect, it } from 'vitest'
import { imagePayload } from './App'

const base = () => ({
  positivePrompt: 'stage', negativePrompt: '', checkpoint: 'model',
  width: '512', height: '512', steps: '20', cfg: '7', seed: '0',
  loras: [{ name: 'detail', strengthModel: '1', strengthClip: '1' }],
})

describe('image numeric draft', () => {
  it('allows intermediate empty fields but submits only complete numeric values', () => {
    const draft = base()
    draft.seed = ''
    expect(imagePayload(draft)).toBeNull()
    draft.seed = '5'
    expect(imagePayload(draft)?.seed).toBe(5)
    expect(typeof imagePayload(draft)?.seed).toBe('number')
  })

  it('preserves decimals and rejects out-of-range or misaligned values', () => {
    const draft = base()
    draft.cfg = '7.25'
    draft.loras[0].strengthModel = '-0.35'
    expect(imagePayload(draft)?.loras[0].strengthModel).toBe(-0.35)
    draft.width = '513'
    expect(imagePayload(draft)).toBeNull()
    draft.width = '512'
    draft.steps = '151'
    expect(imagePayload(draft)).toBeNull()
    draft.steps = '20'
    draft.loras[0].strengthClip = ''
    expect(imagePayload(draft)).toBeNull()
  })
})
