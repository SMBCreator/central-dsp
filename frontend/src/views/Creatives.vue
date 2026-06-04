<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Creatives</h1>
        <p class="page-subtitle">Upload and manage ad creatives with format validation</p>
      </div>
      <button @click="openUpload()" class="btn btn-primary">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12l7-7 7 7"/></svg>
        Upload Creative
      </button>
    </div>

    <!-- Filter bar -->
    <div class="filter-bar">
      <div class="search-wrap">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input v-model="search" class="search-input" placeholder="Search creatives..." />
      </div>
      <div class="chip-group">
        <button v-for="f in ['All','Banner','Native','Video']" :key="f"
          @click="filterFormat = f"
          :class="['chip', filterFormat === f ? 'active' : '']">
          {{ f }}
        </button>
      </div>
      <div class="chip-group">
        <button v-for="s in ['All','Active','Pending','Rejected']" :key="s"
          @click="filterStatus = s"
          :class="['chip', filterStatus === s ? 'active' : '']">
          {{ s }}
        </button>
      </div>
    </div>

    <!-- Grid -->
    <div class="creatives-grid">
      <div v-for="cr in filtered" :key="cr.id" class="cr-card"
        @click="openPreview(cr)">
        <!-- Thumbnail -->
        <div class="cr-thumb" :style="thumbStyle(cr)">
          <div v-if="cr.format === 'video'" class="play-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </div>
          <div v-if="cr.format === 'native' && cr.cropZone" class="crop-indicator">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 2v14h14M2 6h14v14"/></svg>
            Cropped
          </div>
          <div :class="`audit-badge audit-${cr.auditStatus}`">{{ auditLabel(cr.auditStatus) }}</div>
        </div>
        <!-- Info -->
        <div class="cr-info">
          <div class="cr-name">{{ cr.name }}</div>
          <div class="cr-meta">
            <span :class="`format-tag fmt-${cr.format}`">{{ cr.format }}</span>
            <span class="cr-size">{{ cr.width }}×{{ cr.height }}</span>
            <span class="cr-weight">{{ fmtBytes(cr.fileSize) }}</span>
          </div>
          <div class="cr-campaigns">{{ cr.campaignCount }} campaign{{ cr.campaignCount !== 1 ? 's' : '' }}</div>
        </div>
        <div class="cr-footer">
          <button @click.stop="openEdit(cr)" class="btn btn-ghost btn-xs">Edit</button>
          <button @click.stop="openUpload(cr)" class="btn btn-ghost btn-xs">Replace</button>
        </div>
      </div>

      <!-- Empty -->
      <div v-if="filtered.length === 0" class="empty-state" style="grid-column:1/-1">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:#333"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="m9 9 6 6M15 9l-6 6"/></svg>
        <div style="color:#555;margin-top:8px">No creatives found</div>
        <button @click="openUpload()" class="btn btn-ghost btn-sm" style="margin-top:10px">Upload first creative</button>
      </div>
    </div>

    <!-- ═══════════════════════════════════════════════════════════
         UPLOAD / EDITOR MODAL
    ═══════════════════════════════════════════════════════════ -->
    <Transition name="modal">
    <div v-if="showUpload" class="modal-backdrop" @click.self="closeUpload">
      <div class="modal-panel upload-panel">

        <div class="modal-header">
          <div class="modal-title">{{ editCreative ? 'Edit Creative' : 'Upload Creative' }}</div>
          <button @click="closeUpload" class="btn btn-ghost btn-sm">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>

        <div class="upload-body">

          <!-- LEFT: form -->
          <div class="upload-left">

            <!-- Format selector -->
            <div class="form-section">
              <div class="form-section-title">Format</div>
              <div class="chip-group">
                <button v-for="f in formats" :key="f.v"
                  @click="selectFormat(f.v)"
                  :class="['chip chip-lg', uploadForm.format === f.v ? 'active' : '']">
                  <span>{{ f.icon }}</span> {{ f.l }}
                </button>
              </div>
            </div>

            <!-- Creative name -->
            <div class="form-row">
              <label class="form-label">Creative name</label>
              <input v-model="uploadForm.name" class="input" placeholder="e.g. Homepage Banner 300x250" />
            </div>

            <!-- Campaign bind -->
            <div class="form-row">
              <label class="form-label">Link to campaign</label>
              <select v-model="uploadForm.campaignId" class="input">
                <option value="">— Not linked —</option>
                <option value="1">Summer Sale 2026</option>
                <option value="2">Brand Awareness Q2</option>
                <option value="3">Retargeting – Cart Abandonment</option>
              </select>
            </div>

            <!-- ══════════════════ BANNER FIELDS ══════════════════ -->
            <template v-if="uploadForm.format === 'banner'">
              <div class="form-section">
                <div class="form-section-title">Size</div>
                <div class="chip-group" style="flex-wrap:wrap">
                  <button v-for="sz in bannerSizes" :key="sz.label"
                    @click="uploadForm.size = sz.label"
                    :class="['chip', uploadForm.size === sz.label ? 'active' : '']">
                    {{ sz.label }}
                    <span style="font-size:9px;opacity:0.5;margin-left:4px">{{ sz.note }}</span>
                  </button>
                </div>
              </div>

              <!-- Drop zone -->
              <div class="form-section">
                <div class="form-section-title">Image / HTML5</div>
                <div :class="['drop-zone', dragOver ? 'drag-over' : '', bannerFile ? 'has-file' : '']"
                  @dragover.prevent="dragOver=true"
                  @dragleave="dragOver=false"
                  @drop.prevent="onDrop($event,'banner')"
                  @click="$refs.bannerInput.click()">
                  <input ref="bannerInput" type="file" hidden
                    accept="image/jpeg,image/png,image/gif,image/webp,application/zip,text/html"
                    @change="onFilePick($event,'banner')" />
                  <template v-if="!bannerFile">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:#333"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    <div style="font-size:12px;color:#555;margin-top:6px">Drag & drop or click to browse</div>
                    <div style="font-size:10px;color:#333;margin-top:3px">JPG, PNG, GIF, WebP, HTML5 (ZIP) · max 150 KB</div>
                  </template>
                  <template v-else>
                    <img v-if="bannerPreviewUrl" :src="bannerPreviewUrl" style="max-height:80px;max-width:100%;border-radius:4px;" />
                    <div style="font-size:11px;color:#888;margin-top:6px">{{ bannerFile.name }}</div>
                    <button @click.stop="clearFile('banner')" class="btn btn-ghost btn-xs" style="margin-top:4px">Remove</button>
                  </template>
                </div>
              </div>

              <!-- Click URL -->
              <div class="form-row">
                <label class="form-label">Click URL</label>
                <input v-model="uploadForm.clickUrl" class="input" placeholder="https://example.com/landing" />
              </div>
            </template>

            <!-- ══════════════════ NATIVE FIELDS ══════════════════ -->
            <template v-if="uploadForm.format === 'native'">
              <!-- Image upload with crop -->
              <div class="form-section">
                <div class="form-section-title">Main Image
                  <span class="spec-hint">1200×628px recommended · max 500 KB · JPG/PNG/WebP</span>
                </div>
                <div :class="['drop-zone', dragOverNative ? 'drag-over' : '', nativeFile ? 'has-file' : '']"
                  @dragover.prevent="dragOverNative=true"
                  @dragleave="dragOverNative=false"
                  @drop.prevent="onDrop($event,'native')"
                  @click="!nativeFile && $refs.nativeInput.click()">
                  <input ref="nativeInput" type="file" hidden
                    accept="image/jpeg,image/png,image/webp"
                    @change="onFilePick($event,'native')" />
                  <template v-if="!nativeFile">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:#333"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                    <div style="font-size:12px;color:#555;margin-top:6px">Upload main image</div>
                  </template>
                  <template v-else>
                    <!-- CROP ZONE -->
                    <div class="crop-wrapper" ref="cropWrapper">
                      <img :src="nativePreviewUrl" ref="cropImg"
                        style="width:100%;display:block;user-select:none;border-radius:4px"
                        @load="initCrop" />
                      <!-- Crop overlay -->
                      <div class="crop-overlay"
                        :style="cropOverlayStyle"
                        @mousedown.prevent="startCropDrag($event,'move')">
                        <!-- Resize handles -->
                        <div class="crop-handle tl" @mousedown.stop.prevent="startCropDrag($event,'tl')"/>
                        <div class="crop-handle tr" @mousedown.stop.prevent="startCropDrag($event,'tr')"/>
                        <div class="crop-handle bl" @mousedown.stop.prevent="startCropDrag($event,'bl')"/>
                        <div class="crop-handle br" @mousedown.stop.prevent="startCropDrag($event,'br')"/>
                        <!-- Grid lines -->
                        <div class="crop-grid">
                          <div v-for="i in 4" :key="i" class="crop-grid-line h" :style="{top: (i*25)+'%'}"/>
                          <div v-for="i in 4" :key="'v'+i" class="crop-grid-line v" :style="{left: (i*25)+'%'}"/>
                        </div>
                        <div class="crop-dims">{{ cropDimsLabel }}</div>
                      </div>
                    </div>
                    <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
                      <button @click.stop="setCropPreset('1:1')" class="btn btn-ghost btn-xs">1:1 Square</button>
                      <button @click.stop="setCropPreset('4:3')" class="btn btn-ghost btn-xs">4:3</button>
                      <button @click.stop="setCropPreset('16:9')" class="btn btn-ghost btn-xs">16:9</button>
                      <button @click.stop="setCropPreset('1.91:1')" class="btn btn-ghost btn-xs">1.91:1 Feed</button>
                      <button @click.stop="clearFile('native')" class="btn btn-ghost btn-xs" style="color:#f87171">Remove</button>
                    </div>
                  </template>
                </div>
              </div>

              <!-- Logo -->
              <div class="form-row">
                <label class="form-label">Logo / Brand icon
                  <span class="spec-hint">100×100 · max 50 KB</span>
                </label>
                <div class="drop-zone drop-zone-sm" @click="$refs.logoInput.click()">
                  <input ref="logoInput" type="file" hidden accept="image/*" @change="onFilePick($event,'logo')" />
                  <span style="font-size:11px;color:#555">{{ logoFile ? logoFile.name : 'Click to upload logo' }}</span>
                </div>
              </div>

              <!-- Native text fields -->
              <div class="form-row">
                <label class="form-label">
                  Title
                  <span class="char-counter" :class="nativeTitleWarn ? 'warn' : ''">
                    {{ uploadForm.nativeTitle.length }}/{{ nativeTitleMax }}
                  </span>
                </label>
                <input v-model="uploadForm.nativeTitle" class="input"
                  :maxlength="nativeTitleMax"
                  placeholder="Short compelling headline" />
                <div v-if="nativeTitleWarn" class="field-warn">⚠ Recommended max {{ nativeTitleMax }} chars</div>
              </div>

              <div class="form-row">
                <label class="form-label">
                  Description
                  <span class="char-counter" :class="nativeDescWarn ? 'warn' : ''">
                    {{ uploadForm.nativeDesc.length }}/{{ nativeDescMax }}
                  </span>
                </label>
                <textarea v-model="uploadForm.nativeDesc" class="input textarea"
                  :maxlength="nativeDescMax"
                  placeholder="Supporting description text"/>
                <div v-if="nativeDescWarn" class="field-warn">⚠ Recommended max {{ nativeDescMax }} chars</div>
              </div>

              <div class="form-row">
                <label class="form-label">
                  CTA Button text
                  <span class="char-counter" :class="uploadForm.nativeCta.length > 15 ? 'warn' : ''">
                    {{ uploadForm.nativeCta.length }}/15
                  </span>
                </label>
                <input v-model="uploadForm.nativeCta" class="input" maxlength="15"
                  placeholder="Learn More" />
              </div>

              <div class="form-row">
                <label class="form-label">Advertiser name
                  <span class="spec-hint">Displayed as "Ad by ..."</span>
                </label>
                <input v-model="uploadForm.advertiserName" class="input" maxlength="25"
                  placeholder="Your Brand" />
              </div>

              <div class="form-row">
                <label class="form-label">Click URL</label>
                <input v-model="uploadForm.clickUrl" class="input" placeholder="https://" />
              </div>
            </template>

            <!-- ══════════════════ VIDEO FIELDS ══════════════════ -->
            <template v-if="uploadForm.format === 'video'">
              <div class="form-section">
                <div class="form-section-title">Video file
                  <span class="spec-hint">MP4 (H.264) · max 50 MB · 15s/30s recommended</span>
                </div>
                <div :class="['drop-zone', dragOverVideo ? 'drag-over' : '', videoFile ? 'has-file' : '']"
                  @dragover.prevent="dragOverVideo=true"
                  @dragleave="dragOverVideo=false"
                  @drop.prevent="onDrop($event,'video')"
                  @click="$refs.videoInput.click()">
                  <input ref="videoInput" type="file" hidden
                    accept="video/mp4,video/webm"
                    @change="onFilePick($event,'video')" />
                  <template v-if="!videoFile">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:#333"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
                    <div style="font-size:12px;color:#555;margin-top:6px">MP4 or WebM · max 50 MB</div>
                  </template>
                  <template v-else>
                    <video v-if="videoPreviewUrl" :src="videoPreviewUrl"
                      style="max-height:120px;max-width:100%;border-radius:4px;" controls />
                    <div style="font-size:11px;color:#888;margin-top:6px">{{ videoFile.name }} · {{ fmtBytes(videoFile.size) }}</div>
                    <button @click.stop="clearFile('video')" class="btn btn-ghost btn-xs" style="margin-top:4px;color:#f87171">Remove</button>
                  </template>
                </div>
              </div>

              <div class="form-section">
                <div class="form-section-title">Video specs</div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                  <div class="form-row">
                    <label class="form-label">Duration (sec)</label>
                    <input v-model.number="uploadForm.videoDuration" type="number" class="input" placeholder="15" />
                    <div v-if="videoDurationWarn" class="field-warn">{{ videoDurationWarn }}</div>
                  </div>
                  <div class="form-row">
                    <label class="form-label">Skip after (sec)</label>
                    <input v-model.number="uploadForm.videoSkipAfter" type="number" class="input" placeholder="5" />
                  </div>
                </div>
                <div class="form-row" style="margin-top:8px">
                  <label class="form-label">Aspect ratio</label>
                  <div class="chip-group">
                    <button v-for="ar in ['16:9','9:16','1:1','4:3']" :key="ar"
                      @click="uploadForm.videoAspect = ar"
                      :class="['chip', uploadForm.videoAspect === ar ? 'active' : '']">
                      {{ ar }}
                    </button>
                  </div>
                </div>
                <div class="form-row">
                  <label class="form-label">Companion banner (optional)
                    <span class="spec-hint">300×60 or 300×250 · max 50 KB</span>
                  </label>
                  <div class="drop-zone drop-zone-sm" @click="$refs.companionInput.click()">
                    <input ref="companionInput" type="file" hidden accept="image/*" @change="onFilePick($event,'companion')" />
                    <span style="font-size:11px;color:#555">{{ companionFile ? companionFile.name : 'Upload companion banner' }}</span>
                  </div>
                </div>
              </div>

              <div class="form-row">
                <label class="form-label">Click URL</label>
                <input v-model="uploadForm.clickUrl" class="input" placeholder="https://" />
              </div>
            </template>

          </div>

          <!-- RIGHT: validation panel -->
          <div class="upload-right">
            <div class="val-panel">
              <div class="val-title">Validation
                <span :class="`val-badge ${allPassed ? 'ok' : hasErrors ? 'err' : 'warn'}`">
                  {{ allPassed ? 'Ready' : hasErrors ? 'Errors' : 'Warnings' }}
                </span>
              </div>

              <div v-for="rule in activeRules" :key="rule.id" class="val-row">
                <div :class="`val-icon val-${rule.status}`">
                  <svg v-if="rule.status === 'ok'" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                  <svg v-else-if="rule.status === 'err'" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6 6 18M6 6l12 12"/></svg>
                  <svg v-else-if="rule.status === 'warn'" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/></svg>
                  <svg v-else width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/></svg>
                </div>
                <div class="val-text">
                  <div class="val-label">{{ rule.label }}</div>
                  <div class="val-desc">{{ rule.desc }}</div>
                </div>
              </div>

              <!-- Spec reference card -->
              <div class="spec-card">
                <div class="spec-card-title">{{ specTitle }}</div>
                <div v-for="s in specLines" :key="s.k" class="spec-line">
                  <span class="spec-key">{{ s.k }}</span>
                  <span class="spec-val">{{ s.v }}</span>
                </div>
              </div>
            </div>
          </div>

        </div>

        <div class="modal-footer">
          <button @click="closeUpload" class="btn btn-secondary">Cancel</button>
          <button @click="submitCreative"
            :disabled="hasErrors || saving || !uploadForm.name"
            class="btn btn-primary">
            {{ saving ? 'Uploading...' : (editCreative ? 'Save Changes' : 'Upload Creative') }}
          </button>
        </div>
      </div>
    </div>
    </Transition>

    <!-- Preview modal -->
    <Transition name="modal">
    <div v-if="previewCreative" class="modal-backdrop" @click.self="previewCreative=null">
      <div class="preview-panel">
        <div class="modal-header">
          <div class="modal-title">{{ previewCreative.name }}</div>
          <button @click="previewCreative=null" class="btn btn-ghost btn-sm">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div style="padding:24px;display:flex;gap:24px;flex-wrap:wrap">
          <div style="flex:1;min-width:220px">
            <div class="cr-thumb-lg" :style="thumbStyle(previewCreative)"/>
          </div>
          <div style="flex:1;min-width:200px;display:flex;flex-direction:column;gap:8px">
            <div class="spec-line"><span class="spec-key">Format</span><span class="spec-val">{{ previewCreative.format }}</span></div>
            <div class="spec-line"><span class="spec-key">Size</span><span class="spec-val">{{ previewCreative.width }}×{{ previewCreative.height }}</span></div>
            <div class="spec-line"><span class="spec-key">File size</span><span class="spec-val">{{ fmtBytes(previewCreative.fileSize) }}</span></div>
            <div class="spec-line"><span class="spec-key">Status</span>
              <span :class="`audit-badge audit-${previewCreative.auditStatus}`" style="position:static;font-size:11px">{{ auditLabel(previewCreative.auditStatus) }}</span>
            </div>
            <div v-if="previewCreative.nativeTitle" class="spec-line"><span class="spec-key">Title</span><span class="spec-val">{{ previewCreative.nativeTitle }}</span></div>
            <div v-if="previewCreative.nativeDesc" class="spec-line"><span class="spec-key">Desc</span><span class="spec-val">{{ previewCreative.nativeDesc }}</span></div>
            <div v-if="previewCreative.nativeCta" class="spec-line"><span class="spec-key">CTA</span><span class="spec-val">{{ previewCreative.nativeCta }}</span></div>
          </div>
        </div>
      </div>
    </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'

// ─────────── Constants ───────────
const nativeTitleMax = 30
const nativeDescMax  = 90

const bannerSizes = [
  { label:'300×250', note:'Leaderboard', maxKB:150 },
  { label:'728×90',  note:'Banner',      maxKB:150 },
  { label:'320×50',  note:'Mobile',      maxKB:100 },
  { label:'320×480', note:'Interstitial',maxKB:150 },
  { label:'300×600', note:'Half Page',   maxKB:150 },
  { label:'160×600', note:'Wide Sky',    maxKB:150 },
  { label:'970×250', note:'Billboard',   maxKB:200 },
  { label:'1200×628',note:'Feed',        maxKB:200 },
]

const formats = [
  { v:'banner', l:'Banner',  icon:'🖼' },
  { v:'native', l:'Native',  icon:'📰' },
  { v:'video',  l:'Video',   icon:'🎬' },
]

// ─────────── State ───────────
const showUpload      = ref(false)
const editCreative    = ref(null)
const previewCreative = ref(null)
const saving          = ref(false)
const search          = ref('')
const filterFormat    = ref('All')
const filterStatus    = ref('All')
const dragOver        = ref(false)
const dragOverNative  = ref(false)
const dragOverVideo   = ref(false)

const bannerFile      = ref(null)
const bannerPreviewUrl= ref(null)
const nativeFile      = ref(null)
const nativePreviewUrl= ref(null)
const logoFile        = ref(null)
const videoFile       = ref(null)
const videoPreviewUrl = ref(null)
const companionFile   = ref(null)

// Crop state
const cropWrapper = ref(null)
const cropImg     = ref(null)
const crop = ref({ x:10, y:10, w:80, h:80 })  // % values
const cropDragging = ref(null)
const cropDragStart= ref(null)

const uploadForm = ref(defaultForm())
function defaultForm() {
  return {
    format: 'banner', name: '', campaignId: '',
    size: '300×250', clickUrl: '',
    nativeTitle: '', nativeDesc: '', nativeCta: '', advertiserName: '',
    videoDuration: 15, videoSkipAfter: 5, videoAspect: '16:9',
  }
}

// ─────────── Mock creatives ───────────
const creatives = ref([
  { id:1, name:'Summer 300×250', format:'banner', width:300, height:250,
    fileSize:42000, campaignCount:2, auditStatus:'approved',
    thumb:'https://via.placeholder.com/300x250/1a1a2e/60a5fa?text=Banner' },
  { id:2, name:'Feed Native – Sale', format:'native', width:1200, height:628,
    fileSize:185000, campaignCount:1, auditStatus:'pending',
    nativeTitle:'Big Summer Sale', nativeDesc:'Up to 70% off all items', nativeCta:'Shop Now',
    cropZone:{ x:10, y:5, w:80, h:90 },
    thumb:'https://via.placeholder.com/1200x628/1a1a1a/a78bfa?text=Native' },
  { id:3, name:'Pre-roll 16:9 15s', format:'video', width:1920, height:1080,
    fileSize:8500000, campaignCount:3, auditStatus:'approved',
    thumb:'https://via.placeholder.com/480x270/111/34d399?text=Video' },
  { id:4, name:'Mobile Banner 320×50', format:'banner', width:320, height:50,
    fileSize:22000, campaignCount:1, auditStatus:'rejected',
    thumb:'https://via.placeholder.com/320x50/1a1a1a/f87171?text=Mobile' },
])

// ─────────── Computed ───────────
const filtered = computed(() => {
  return creatives.value.filter(c => {
    const fmtOk  = filterFormat.value === 'All' || c.format === filterFormat.value.toLowerCase()
    const stOk   = filterStatus.value === 'All' ||
      (filterStatus.value === 'Active'   && c.auditStatus === 'approved') ||
      (filterStatus.value === 'Pending'  && c.auditStatus === 'pending') ||
      (filterStatus.value === 'Rejected' && c.auditStatus === 'rejected')
    const srchOk = !search.value || c.name.toLowerCase().includes(search.value.toLowerCase())
    return fmtOk && stOk && srchOk
  })
})

// ─────────── Validation rules ───────────
const activeRules = computed(() => {
  const f = uploadForm.value.format
  const rules = []

  // Name
  rules.push({
    id:'name', label:'Creative name',
    status: uploadForm.value.name ? 'ok' : 'pending',
    desc: uploadForm.value.name ? uploadForm.value.name : 'Required',
  })

  if (f === 'banner') {
    const sel = bannerSizes.find(s => s.label === uploadForm.value.size)
    const maxKB = sel?.maxKB || 150

    rules.push({ id:'bsize', label:'Banner size selected',
      status: uploadForm.value.size ? 'ok' : 'err',
      desc: uploadForm.value.size || 'Select a size' })

    if (bannerFile.value) {
      const kb = bannerFile.value.size / 1024
      rules.push({ id:'bweight', label:'File weight',
        status: kb <= maxKB ? 'ok' : 'err',
        desc: `${kb.toFixed(0)} KB / ${maxKB} KB max` })

      // Check real dimensions vs selected size
      if (bannerImageDims.value) {
        const [sw,sh] = (uploadForm.value.size || '').split('×').map(Number)
        const dimOk = bannerImageDims.value.w === sw && bannerImageDims.value.h === sh
        rules.push({ id:'bdims', label:'Image dimensions',
          status: dimOk ? 'ok' : 'err',
          desc: dimOk
            ? `${sw}×${sh}px ✓`
            : `Got ${bannerImageDims.value.w}×${bannerImageDims.value.h} — expected ${sw}×${sh}` })
      }

      const ext = bannerFile.value.name.split('.').pop().toLowerCase()
      const animOk = !['gif'].includes(ext) || bannerFile.value.size/1024 <= maxKB
      rules.push({ id:'bformat', label:'File format',
        status: ['jpg','jpeg','png','gif','webp','zip','html'].includes(ext) ? 'ok' : 'err',
        desc: `${ext.toUpperCase()} — allowed: JPG PNG GIF WebP HTML5(ZIP)` })
    } else {
      rules.push({ id:'bfile', label:'Creative file', status:'pending', desc:'No file selected' })
    }

    rules.push({ id:'burl', label:'Click URL',
      status: isValidUrl(uploadForm.value.clickUrl) ? 'ok' : (uploadForm.value.clickUrl ? 'err' : 'pending'),
      desc: uploadForm.value.clickUrl || 'Required' })
  }

  if (f === 'native') {
    if (nativeFile.value) {
      const kb = nativeFile.value.size / 1024
      rules.push({ id:'nweight', label:'Image file size',
        status: kb <= 500 ? 'ok' : 'err',
        desc: `${kb.toFixed(0)} KB / 500 KB max` })
      if (nativeImageDims.value) {
        const { w, h } = nativeImageDims.value
        const ratio = w / h
        const ratioOk = ratio >= 1.3 && ratio <= 2.1
        rules.push({ id:'ndims', label:'Image dimensions',
          status: (w >= 600 && h >= 314) ? 'ok' : 'warn',
          desc: `${w}×${h}px — ${w >= 600 && h >= 314 ? 'good' : 'min 600×314 recommended'}` })
        rules.push({ id:'nratio', label:'Aspect ratio',
          status: ratioOk ? 'ok' : 'warn',
          desc: `${ratio.toFixed(2)}:1 — ${ratioOk ? 'standard' : 'non-standard ratio'}` })
      }
      rules.push({ id:'ncrop', label:'Crop zone',
        status: 'ok',
        desc: `Visible area: ${crop.value.x.toFixed(0)}% ${crop.value.y.toFixed(0)}% / ${crop.value.w.toFixed(0)}% ${crop.value.h.toFixed(0)}%` })
    } else {
      rules.push({ id:'nfile', label:'Main image', status:'pending', desc:'Required' })
    }

    const tl = uploadForm.value.nativeTitle.length
    rules.push({ id:'ntitle', label:'Title length',
      status: tl === 0 ? 'pending' : tl <= nativeTitleMax ? 'ok' : 'err',
      desc: `${tl} / ${nativeTitleMax} chars ${tl > nativeTitleMax ? '— too long' : ''}` })

    const dl = uploadForm.value.nativeDesc.length
    rules.push({ id:'ndesc', label:'Description length',
      status: dl === 0 ? 'pending' : dl <= nativeDescMax ? 'ok' : 'err',
      desc: `${dl} / ${nativeDescMax} chars` })

    rules.push({ id:'ncta', label:'CTA button',
      status: uploadForm.value.nativeCta ? 'ok' : 'warn',
      desc: uploadForm.value.nativeCta || 'Recommended (e.g. "Learn More")' })

    rules.push({ id:'nadv', label:'Advertiser name',
      status: uploadForm.value.advertiserName ? 'ok' : 'warn',
      desc: uploadForm.value.advertiserName || 'Shown as "Ad by ..."' })

    if (logoFile.value) {
      const kb = logoFile.value.size / 1024
      rules.push({ id:'nlogo', label:'Logo file size',
        status: kb <= 50 ? 'ok' : 'err',
        desc: `${kb.toFixed(0)} KB / 50 KB max` })
    }

    rules.push({ id:'nurl', label:'Click URL',
      status: isValidUrl(uploadForm.value.clickUrl) ? 'ok' : (uploadForm.value.clickUrl ? 'err' : 'pending'),
      desc: uploadForm.value.clickUrl || 'Required' })
  }

  if (f === 'video') {
    if (videoFile.value) {
      const mb = videoFile.value.size / 1024 / 1024
      rules.push({ id:'vweight', label:'Video file size',
        status: mb <= 50 ? 'ok' : 'err',
        desc: `${mb.toFixed(1)} MB / 50 MB max` })
      const ext = videoFile.value.name.split('.').pop().toLowerCase()
      rules.push({ id:'vformat', label:'File format',
        status: ['mp4','webm'].includes(ext) ? 'ok' : 'err',
        desc: `${ext.toUpperCase()} — allowed: MP4 WebM` })
    } else {
      rules.push({ id:'vfile', label:'Video file', status:'pending', desc:'Required' })
    }

    const dur = uploadForm.value.videoDuration
    rules.push({ id:'vdur', label:'Duration',
      status: dur <= 0 ? 'pending' : dur <= 30 ? 'ok' : dur <= 60 ? 'warn' : 'err',
      desc: `${dur}s — ${dur <= 15 ? 'optimal (≤15s)' : dur <= 30 ? 'standard (≤30s)' : dur <= 60 ? 'long (>30s, may reduce fill)' : 'too long (>60s)'}` })

    const skip = uploadForm.value.videoSkipAfter
    rules.push({ id:'vskip', label:'Skip offset',
      status: skip >= 5 ? 'ok' : 'warn',
      desc: `Skip after ${skip}s — ${skip >= 5 ? 'standard (≥5s)' : 'below recommended 5s'}` })

    rules.push({ id:'vurl', label:'Click URL',
      status: isValidUrl(uploadForm.value.clickUrl) ? 'ok' : (uploadForm.value.clickUrl ? 'err' : 'pending'),
      desc: uploadForm.value.clickUrl || 'Required' })
  }

  return rules
})

const hasErrors  = computed(() => activeRules.value.some(r => r.status === 'err'))
const allPassed  = computed(() => activeRules.value.every(r => r.status === 'ok'))

// ─────────── Spec reference ───────────
const specTitle = computed(() => ({
  banner:'Banner Specs Reference', native:'Native Ad Specs Reference', video:'Video Specs Reference'
}[uploadForm.value.format]))

const specLines = computed(() => {
  const f = uploadForm.value.format
  if (f === 'banner') return [
    { k:'Formats',   v:'JPG, PNG, GIF, WebP, HTML5 (ZIP)' },
    { k:'Max weight',v:'150 KB (HTML5: 200 KB)' },
    { k:'Animation', v:'GIF loops max 3×, HTML5 30s max' },
    { k:'Top sizes',  v:'300×250, 728×90, 320×50, 300×600' },
    { k:'Click URL', v:'HTTPS required' },
    { k:'Margins',   v:'1px border if background is white' },
  ]
  if (f === 'native') return [
    { k:'Image min',  v:'600×314 px' },
    { k:'Image rec',  v:'1200×628 px (1.91:1)' },
    { k:'Image max',  v:'500 KB, JPG/PNG/WebP' },
    { k:'Logo',       v:'100×100 px, max 50 KB' },
    { k:'Title',      v:`max ${nativeTitleMax} chars` },
    { k:'Description',v:`max ${nativeDescMax} chars` },
    { k:'CTA',        v:'max 15 chars' },
    { k:'Advertiser', v:'max 25 chars' },
    { k:'Crop',       v:'Drag overlay to define safe zone' },
  ]
  if (f === 'video') return [
    { k:'Format',     v:'MP4 (H.264 + AAC), WebM' },
    { k:'Max size',   v:'50 MB' },
    { k:'Resolution', v:'min 640×360, rec 1920×1080' },
    { k:'Duration',   v:'15s optimal, 30s standard, max 60s' },
    { k:'Bitrate',    v:'rec 2–5 Mbps' },
    { k:'Skip',       v:'VAST skip offset ≥5s' },
    { k:'Companion',  v:'300×60 or 300×250, max 50 KB' },
    { k:'Audio',      v:'Stereo, 44.1 kHz, loudness ≤ -14 LUFS' },
  ]
  return []
})

// ─────────── Image dimensions check ───────────
const bannerImageDims = ref(null)
const nativeImageDims = ref(null)

function loadImageDims(file, target) {
  const url = URL.createObjectURL(file)
  const img = new Image()
  img.onload = () => { target.value = { w: img.naturalWidth, h: img.naturalHeight }; URL.revokeObjectURL(url) }
  img.src = url
}

// ─────────── File handlers ───────────
function onDrop(e, type) {
  dragOver.value = false; dragOverNative.value = false; dragOverVideo.value = false
  const file = e.dataTransfer.files[0]
  if (file) handleFile(file, type)
}
function onFilePick(e, type) {
  const file = e.target.files[0]
  if (file) handleFile(file, type)
}
function handleFile(file, type) {
  if (type === 'banner') {
    bannerFile.value = file
    bannerImageDims.value = null
    if (file.type.startsWith('image/')) {
      bannerPreviewUrl.value = URL.createObjectURL(file)
      loadImageDims(file, bannerImageDims)
    } else {
      bannerPreviewUrl.value = null
    }
  } else if (type === 'native') {
    nativeFile.value = file
    nativeImageDims.value = null
    nativePreviewUrl.value = URL.createObjectURL(file)
    loadImageDims(file, nativeImageDims)
    nextTick(() => initCrop())
  } else if (type === 'video') {
    videoFile.value = file
    videoPreviewUrl.value = URL.createObjectURL(file)
  } else if (type === 'logo') {
    logoFile.value = file
  } else if (type === 'companion') {
    companionFile.value = file
  }
}
function clearFile(type) {
  if (type === 'banner') { bannerFile.value = null; bannerPreviewUrl.value = null; bannerImageDims.value = null }
  if (type === 'native') { nativeFile.value = null; nativePreviewUrl.value = null; nativeImageDims.value = null }
  if (type === 'video')  { videoFile.value  = null; videoPreviewUrl.value  = null }
}

// ─────────── Crop logic ───────────
function initCrop() {
  crop.value = { x:5, y:5, w:90, h:90 }
}

const cropOverlayStyle = computed(() => ({
  position:'absolute',
  left:   crop.value.x + '%',
  top:    crop.value.y + '%',
  width:  crop.value.w + '%',
  height: crop.value.h + '%',
  cursor: 'move',
}))

const cropDimsLabel = computed(() => {
  if (!nativeImageDims.value) return ''
  const { w, h } = nativeImageDims.value
  return `${Math.round(w * crop.value.w/100)}×${Math.round(h * crop.value.h/100)}px`
})

function startCropDrag(e, handle) {
  cropDragging.value = handle
  cropDragStart.value = { x: e.clientX, y: e.clientY, crop: { ...crop.value } }
  window.addEventListener('mousemove', onCropMouseMove)
  window.addEventListener('mouseup',   onCropMouseUp)
}

function onCropMouseMove(e) {
  if (!cropDragging.value || !cropWrapper.value) return
  const rect  = cropWrapper.value.getBoundingClientRect()
  const dx = (e.clientX - cropDragStart.value.x) / rect.width  * 100
  const dy = (e.clientY - cropDragStart.value.y) / rect.height * 100
  const c  = { ...cropDragStart.value.crop }
  const h  = cropDragging.value

  if (h === 'move') {
    c.x = Math.max(0, Math.min(100 - c.w, c.x + dx))
    c.y = Math.max(0, Math.min(100 - c.h, c.y + dy))
  } else {
    if (h === 'tl' || h === 'bl') { c.w = Math.max(10, c.w - dx); c.x = Math.min(c.x + dx, c.x + c.w - 10) }
    if (h === 'tr' || h === 'br') { c.w = Math.max(10, c.w + dx) }
    if (h === 'tl' || h === 'tr') { c.h = Math.max(10, c.h - dy); c.y = Math.min(c.y + dy, c.y + c.h - 10) }
    if (h === 'bl' || h === 'br') { c.h = Math.max(10, c.h + dy) }
    c.x = Math.max(0, c.x); c.y = Math.max(0, c.y)
    c.w = Math.min(c.w, 100 - c.x)
    c.h = Math.min(c.h, 100 - c.y)
  }
  crop.value = c
}
function onCropMouseUp() {
  cropDragging.value = null
  window.removeEventListener('mousemove', onCropMouseMove)
  window.removeEventListener('mouseup',   onCropMouseUp)
}

function setCropPreset(ratio) {
  const [rw, rh] = ratio.split(':').map(Number)
  const ar = rw / rh
  const maxW = 90, cx = 5
  const h = maxW / ar
  if (h <= 90) {
    crop.value = { x: cx, y: (100 - h) / 2, w: maxW, h }
  } else {
    const w = 90 * ar
    crop.value = { x: (100-w)/2, y: 5, w, h: 90 }
  }
}

// ─────────── Helpers ───────────
const nativeTitleWarn = computed(() => uploadForm.value.nativeTitle.length > nativeTitleMax * 0.9)
const nativeDescWarn  = computed(() => uploadForm.value.nativeDesc.length  > nativeDescMax  * 0.9)
const videoDurationWarn = computed(() => {
  const d = uploadForm.value.videoDuration
  if (d > 60) return '⚠ Duration > 60s — many SSPs will skip'
  if (d > 30) return '⚠ Duration > 30s — reduced fill rate'
  return null
})

function isValidUrl(url) {
  try { return ['http:','https:'].includes(new URL(url).protocol) } catch { return false }
}

function fmtBytes(b) {
  if (!b) return '0 B'
  if (b < 1024) return b + ' B'
  if (b < 1024*1024) return (b/1024).toFixed(0) + ' KB'
  return (b/1024/1024).toFixed(1) + ' MB'
}

function thumbStyle(cr) {
  return {
    backgroundImage: cr.thumb ? `url(${cr.thumb})` : 'none',
    backgroundSize:'cover', backgroundPosition:'center',
    backgroundColor: '#111',
  }
}
function auditLabel(s) {
  return { approved:'Approved', pending:'Pending', rejected:'Rejected' }[s] || s
}

function selectFormat(f) {
  uploadForm.value = { ...defaultForm(), format: f }
  bannerFile.value = null; nativeFile.value = null; videoFile.value = null
  bannerPreviewUrl.value = null; nativePreviewUrl.value = null; videoPreviewUrl.value = null
  bannerImageDims.value = null; nativeImageDims.value = null
}

function openUpload(cr = null) {
  editCreative.value = cr
  uploadForm.value = cr ? { ...defaultForm(), ...cr } : defaultForm()
  showUpload.value = true
}
function openEdit(cr) { openUpload(cr) }
function openPreview(cr) { previewCreative.value = cr }

function closeUpload() {
  showUpload.value = false
  editCreative.value = null
}

async function submitCreative() {
  saving.value = true
  try {
    const fd = new FormData()
    fd.append('data', JSON.stringify({ ...uploadForm.value, cropZone: crop.value }))
    if (bannerFile.value) fd.append('file', bannerFile.value)
    if (nativeFile.value) fd.append('image', nativeFile.value)
    if (logoFile.value)   fd.append('logo', logoFile.value)
    if (videoFile.value)  fd.append('video', videoFile.value)

    const url    = editCreative.value ? `/api/creatives/${editCreative.value.id}` : '/api/creatives'
    const method = editCreative.value ? 'PUT' : 'POST'
    const r = await fetch(url, { method, body: fd })
    if (r.ok) {
      const saved = await r.json()
      if (editCreative.value) {
        const i = creatives.value.findIndex(c => c.id === editCreative.value.id)
        if (i >= 0) creatives.value[i] = { ...creatives.value[i], ...saved }
      } else {
        creatives.value.unshift({ ...saved, auditStatus:'pending', campaignCount:0 })
      }
      closeUpload()
    }
  } catch {}
  finally { saving.value = false }
}

onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onCropMouseMove)
  window.removeEventListener('mouseup',   onCropMouseUp)
})
</script>

<style scoped>
/* ─── Filter ─── */
.filter-bar { display:flex;align-items:center;gap:10px;flex-wrap:wrap; }
.search-wrap {
  display:flex;align-items:center;gap:6px;
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.07);
  border-radius:8px;padding:7px 12px;flex:1;min-width:160px;
}
.search-input { background:none;border:none;outline:none;font-size:12px;color:#ccc;width:100%; }

/* ─── Grid ─── */
.creatives-grid {
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
  gap:10px;
}
.cr-card {
  background:#0e0e0e;border:1px solid rgba(255,255,255,0.06);
  border-radius:12px;overflow:hidden;cursor:pointer;
  transition:border-color 180ms,transform 180ms;
  display:flex;flex-direction:column;
}
.cr-card:hover { border-color:rgba(255,255,255,0.14);transform:translateY(-1px); }

.cr-thumb {
  position:relative;aspect-ratio:4/3;width:100%;
}
.cr-thumb-lg { aspect-ratio:4/3;width:100%;background-size:cover;background-position:center;border-radius:8px; }

.audit-badge {
  position:absolute;bottom:6px;right:6px;
  font-size:9px;font-weight:700;padding:2px 6px;border-radius:4px;letter-spacing:.5px;
}
.audit-approved { background:rgba(52,211,153,0.15);color:#34d399;border:1px solid rgba(52,211,153,0.25); }
.audit-pending  { background:rgba(251,191,36,0.1);color:#fbbf24;border:1px solid rgba(251,191,36,0.2); }
.audit-rejected { background:rgba(248,113,113,0.1);color:#f87171;border:1px solid rgba(248,113,113,0.2); }

.play-icon {
  position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  width:40px;height:40px;border-radius:50%;background:rgba(0,0,0,0.6);
  display:flex;align-items:center;justify-content:center;color:#fff;
}
.crop-indicator {
  position:absolute;top:6px;left:6px;
  display:flex;align-items:center;gap:3px;
  background:rgba(167,139,250,0.15);border:1px solid rgba(167,139,250,0.3);
  color:#a78bfa;font-size:9px;padding:2px 6px;border-radius:4px;font-weight:600;
}

.cr-info { padding:10px 12px;display:flex;flex-direction:column;gap:5px;flex:1; }
.cr-name { font-size:12px;font-weight:600;color:#d0d0d0;line-height:1.3; }
.cr-meta { display:flex;align-items:center;gap:5px;flex-wrap:wrap; }
.cr-size,.cr-weight { font-size:10px;color:#444; }
.cr-campaigns { font-size:10px;color:#333; }
.format-tag { font-size:9px;padding:2px 6px;border-radius:4px;font-weight:600; }
.fmt-banner { background:rgba(96,165,250,0.12);color:#60a5fa; }
.fmt-native { background:rgba(167,139,250,0.12);color:#a78bfa; }
.fmt-video  { background:rgba(52,211,153,0.12);color:#34d399; }

.cr-footer {
  padding:8px 12px;border-top:1px solid rgba(255,255,255,0.05);
  display:flex;gap:6px;
}

/* ─── Upload modal ─── */
.upload-panel {
  max-width:900px;width:100%;max-height:90vh;
  display:flex;flex-direction:column;
}
.upload-body {
  display:grid;grid-template-columns:1fr 280px;
  gap:0;overflow:hidden;flex:1;
}
.upload-left {
  overflow-y:auto;padding:20px 24px;
  display:flex;flex-direction:column;gap:14px;
  border-right:1px solid rgba(255,255,255,0.06);
}
.upload-right { overflow-y:auto;background:#090909; }

/* ─── Drop zone ─── */
.drop-zone {
  border:1.5px dashed rgba(255,255,255,0.1);
  border-radius:10px;padding:20px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  cursor:pointer;transition:border-color 180ms,background 180ms;text-align:center;
  min-height:100px;
}
.drop-zone:hover,.drop-zone.drag-over {
  border-color:rgba(255,255,255,0.25);background:rgba(255,255,255,0.02);
}
.drop-zone.has-file { border-style:solid;border-color:rgba(255,255,255,0.1); }
.drop-zone-sm { min-height:46px;padding:8px 14px; }

/* ─── Crop ─── */
.crop-wrapper {
  position:relative;width:100%;
  background:#000;border-radius:6px;overflow:hidden;
  cursor:crosshair;
}
.crop-overlay {
  position:absolute;
  border:2px solid rgba(255,255,255,0.85);
  box-shadow:0 0 0 9999px rgba(0,0,0,0.45);
  box-sizing:border-box;
}
.crop-handle {
  position:absolute;width:10px;height:10px;
  background:#fff;border-radius:2px;
  cursor:nwse-resize;
}
.crop-handle.tl { top:-5px;left:-5px;cursor:nw-resize; }
.crop-handle.tr { top:-5px;right:-5px;cursor:ne-resize; }
.crop-handle.bl { bottom:-5px;left:-5px;cursor:sw-resize; }
.crop-handle.br { bottom:-5px;right:-5px;cursor:se-resize; }
.crop-grid { position:absolute;inset:0;pointer-events:none; }
.crop-grid-line {
  position:absolute;background:rgba(255,255,255,0.2);
}
.crop-grid-line.h { left:0;right:0;height:1px; }
.crop-grid-line.v { top:0;bottom:0;width:1px; }
.crop-dims {
  position:absolute;bottom:4px;right:6px;
  font-size:10px;color:rgba(255,255,255,0.7);
  background:rgba(0,0,0,0.5);border-radius:3px;padding:1px 5px;
  pointer-events:none;
}

/* ─── Validation panel ─── */
.val-panel { padding:16px;display:flex;flex-direction:column;gap:6px; }
.val-title {
  font-size:11px;font-weight:700;color:#666;text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:4px;
  display:flex;align-items:center;justify-content:space-between;
}
.val-badge {
  font-size:10px;padding:2px 7px;border-radius:4px;font-weight:600;
}
.val-badge.ok  { background:rgba(52,211,153,0.12);color:#34d399; }
.val-badge.err { background:rgba(248,113,113,0.12);color:#f87171; }
.val-badge.warn{ background:rgba(251,191,36,0.12);color:#fbbf24; }

.val-row {
  display:flex;align-items:flex-start;gap:8px;
  padding:6px 8px;border-radius:7px;
  background:rgba(255,255,255,0.02);
  border:1px solid rgba(255,255,255,0.04);
}
.val-icon {
  width:18px;height:18px;border-radius:50%;flex-shrink:0;
  display:flex;align-items:center;justify-content:center;
  margin-top:1px;
}
.val-icon.val-ok      { background:rgba(52,211,153,0.15);color:#34d399; }
.val-icon.val-err     { background:rgba(248,113,113,0.15);color:#f87171; }
.val-icon.val-warn    { background:rgba(251,191,36,0.12);color:#fbbf24; }
.val-icon.val-pending { background:rgba(255,255,255,0.05);color:#555; }
.val-text { flex:1; }
.val-label { font-size:11px;font-weight:600;color:#888; }
.val-desc  { font-size:10px;color:#444;margin-top:1px; }

.spec-card {
  margin-top:10px;background:#0a0a0a;
  border:1px solid rgba(255,255,255,0.06);border-radius:8px;padding:12px;
}
.spec-card-title {
  font-size:10px;font-weight:700;color:#333;text-transform:uppercase;
  letter-spacing:.5px;margin-bottom:8px;
}
.spec-line {
  display:flex;justify-content:space-between;align-items:flex-start;
  gap:8px;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.03);
}
.spec-line:last-child { border-bottom:none; }
.spec-key { font-size:10px;color:#444;flex-shrink:0; }
.spec-val { font-size:10px;color:#666;text-align:right; }
.spec-hint { font-size:9px;color:#444;font-weight:400;margin-left:6px; }

/* ─── Form ─── */
.form-section { display:flex;flex-direction:column;gap:8px; }
.form-section-title {
  font-size:11px;font-weight:700;color:#444;text-transform:uppercase;letter-spacing:.5px;
}
.char-counter { font-size:10px;color:#444;font-weight:400;margin-left:6px; }
.char-counter.warn { color:#fbbf24; }
.field-warn { font-size:10px;color:#fbbf24;margin-top:3px; }
.textarea { min-height:70px;resize:vertical; }

/* ─── Preview modal ─── */
.preview-panel {
  background:#111;border:1px solid rgba(255,255,255,0.09);
  border-radius:14px;width:100%;max-width:640px;max-height:80vh;
  display:flex;flex-direction:column;overflow:hidden;
}

/* ─── Shared ─── */
.modal-backdrop {
  position:fixed;inset:0;background:rgba(0,0,0,0.75);
  backdrop-filter:blur(5px);
  display:flex;align-items:center;justify-content:center;z-index:200;padding:24px;
}
.modal-panel {
  background:#111;border:1px solid rgba(255,255,255,0.09);
  border-radius:14px;box-shadow:0 24px 80px rgba(0,0,0,0.8);
}
.modal-header {
  display:flex;align-items:center;justify-content:space-between;
  padding:18px 24px;border-bottom:1px solid rgba(255,255,255,0.06);flex-shrink:0;
}
.modal-title { font-size:15px;font-weight:600;color:#e0e0e0; }
.modal-footer {
  padding:14px 24px;border-top:1px solid rgba(255,255,255,0.06);
  display:flex;justify-content:flex-end;gap:8px;flex-shrink:0;
}
.modal-enter-active { transition:all 200ms cubic-bezier(0.16,1,0.3,1); }
.modal-leave-active { transition:all 130ms ease-in; }
.modal-enter-from { opacity:0;transform:scale(0.96) translateY(10px); }
.modal-leave-to   { opacity:0;transform:scale(0.98); }
</style>
