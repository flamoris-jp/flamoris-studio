import { describe, expect, it } from 'vitest'
import { imagePayload, initialImageDraft, restoreImageDraft, withRandomSeed } from './App'

const base = () => ({ ...initialImageDraft('model'), positivePrompt: 'stage',
  loras: [{ name: 'detail', strengthModel: '1', strengthClip: '1' }] })

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

  it('preserves long tag prompts and ordered LoRAs with sampler and scheduler', () => {
    const draft = base()
    draft.positivePrompt = 'tag, '.repeat(3000)
    draft.negativePrompt = 'blurry, '.repeat(1800)
    draft.loras.push({ name: 'second', strengthModel: '0.4', strengthClip: '0.2' })
    draft.sampler = 'dpmpp_2m'; draft.scheduler = 'karras'; draft.denoise = '0.8'
    const result = imagePayload(draft)
    expect(result?.positivePrompt).toBe(draft.positivePrompt)
    expect(result?.negativePrompt).toBe(draft.negativePrompt)
    expect(result?.loras.map(item => item.name)).toEqual(['detail', 'second'])
    expect(result?.sampler).toBe('dpmpp_2m')
    expect(result?.scheduler).toBe('karras')
    expect(result?.denoise).toBe(0.8)
    draft.loras = []
    expect(imagePayload(draft)?.loras).toEqual([])
  })

  it('restores settings into an editable draft without dropping unspecified values', () => {
    const draft = base()
    const restored = restoreImageDraft(draft, { positivePrompt: 'again', width: 768, height: 1152,
      seed: 123, sampler: 'euler', scheduler: 'normal', loras: [{ name: 'new', strengthModel: 0.5, strengthClip: 0.8 }] })
    expect(restored.width).toBe('768')
    expect(restored.height).toBe('1152')
    expect(restored.negativePrompt).toBe(draft.negativePrompt)
    expect(restored.loras[0].strengthModel).toBe('0.5')
    expect(imagePayload(restored)?.seed).toBe(123)
    expect(draft.positivePrompt).toBe('stage')
  })

  it('randomizes only the seed', () => {
    const draft = base()
    const changed = withRandomSeed(draft, new Uint32Array([1, 2]))
    expect(changed).toEqual({ ...draft, seed: '2097154' })
  })
})
