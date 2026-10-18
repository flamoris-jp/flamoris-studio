// @vitest-environment jsdom
import React, { act, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, test, vi } from 'vitest'
import { ImageEditor, initialImageDraft, restoreImageDraft } from './App'
import { api, type Workflow } from './api'
import { workflowPayload } from './WorkflowImage'
const handle = '10000000-0000-4000-8000-000000000000'
const item: Workflow = { id:'comfy-'+'c'.repeat(64), name:'Declared editor', kind:'registered-comfy', definitionVersion:1, definitionDigest:'c'.repeat(64), selectable:true, reason:null, recipeSchema:10, referenceRoles:['source'], image:{profile:'declared-comfy-v1',mode:'declared',dimensions:{mode:'graph'}}, parameters:{
 source:{type:'string',role:'managed_input',label:'Source image',required:true,pattern:'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'},
 edit:{type:'string',role:'prompt',label:'Edit instruction',required:true,min_length:1,default:'Smile'},
 intensity:{type:'number',role:'parameter',label:'Intensity',minimum:0,maximum:1,default:0.4}} }
const draft = () => restoreImageDraft(initialImageDraft(), {workflowId:item.id, workflowKind:item.kind,definitionVersion:1,definitionDigest:item.definitionDigest,imageInputs:{source:handle},additionalParameters:{edit:'Wave',intensity:0.7}})
test('declared settings restore and reject missing images, invalid values and stale pins',()=>{
 expect(draft().imageInputs).toEqual({source:handle})
 expect(workflowPayload(draft(),item)).toMatchObject({imageInputs:{source:handle},additionalParameters:{edit:'Wave',intensity:0.7}})
 expect(workflowPayload({...draft(),imageInputs:{}},item)).toBeNull()
 expect(workflowPayload({...draft(),additionalParameters:{edit:'Wave',intensity:2}},item)).toBeNull()
 expect(workflowPayload({...draft(),definitionDigest:'d'.repeat(64)},item)).toBeNull()
})
test.each([true,false])('declared controls use their labels and owned image availability (%s)',async available=>{
 vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT',true)
 vi.spyOn(api,'styles').mockResolvedValue({items:[]})
 vi.spyOn(api,'input').mockResolvedValue({id:handle,available,thumbnailUrl:'/thumb'})
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host),submit=vi.fn()
 function Editor(){const [form,setForm]=useState(draft);return <ImageEditor discovery={{available:true,checkpoints:[],loras:[],templates:[],managedInputReady:true,workflows:[item]}} form={form} setForm={setForm} csrf="csrf" busy={false} onSubmit={submit}/>}
 try{
  await act(async()=>root.render(<Editor/>))
  expect(host.textContent).toContain('Source image');expect(host.textContent).toContain('Edit instruction');expect(host.querySelector('textarea')?.value).toBe('Wave')
  expect([...host.querySelectorAll('label')].some(l=>l.textContent?.startsWith('Positive prompt'))).toBe(false)
  const button=[...host.querySelectorAll('button')].find(b=>b.textContent?.includes('Generate image'))!
  expect(button.disabled).toBe(!available)
  if(available){await act(async()=>button.click());expect(submit).toHaveBeenCalledTimes(1);expect(submit.mock.calls[0][0]).not.toHaveProperty('checkpoint')}
  else expect(submit).not.toHaveBeenCalled()
 }finally{await act(async()=>root.unmount());host.remove();vi.restoreAllMocks();vi.unstubAllGlobals()}
})
