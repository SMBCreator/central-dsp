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

              <!-- Banner type: Static / GIF / HTML5 -->
              <div class="form-section">
                <div class="form-section-title">Creative type</div>
                <div class="chip-group">
                  <button v-for="t in bannerTypes" :key="t.v"
                    @click="uploadForm.bannerType = t.v"
                    :class="['chip', uploadForm.bannerType === t.v ? 'active' : '']">
                    {{ t.icon }} {{ t.l }}
                  </button>
                </div>
                <div class="type-hint">{{ bannerTypeHint }}</div>
              </div>

              <!-- Size groups -->
              <div class="form-section">
                <div class="form-section-title">Size</div>

                <!-- Desktop group -->
                <div class="size-group-label">🖥 Desktop</div>
                <div class="chip-group" style="flex-wrap:wrap;margin-bottom:8px">
                  <button v-for="sz in desktopSizes" :key="sz.label"
                    @click="uploadForm.size = sz.label"
                    :class="['chip chip-size', uploadForm.size === sz.label ? 'active' : '']">
                    <span class="chip-sz-num">{{ sz.label }}</span>
                    <span class="chip-sz-note">{{ sz.note }}</span>
                  </button>
                </div>

                <!-- Mobile group -->
                <div class="size-group-label">📱 Mobile</div>
                <div class="chip-group" style="flex-wrap:wrap;margin-bottom:8px">
                  <button v-for="sz in mobileSizes" :key="sz.label"
                    @click="uploadForm.size = sz.label"
                    :class="['chip chip-size', uploadForm.size === sz.label ? 'active' : '']">
                    <span class="chip-sz-num">{{ sz.label }}</span>
                    <span class="chip-sz-note">{{ sz.note }}</span>
                  </button>
                </div>

                <!-- Rich Media / Special -->
                <div class="size-group-label">⚡ Rich Media / Special</div>
                <div class="chip-group" style="flex-wrap:wrap">
                  <button v-for="sz in specialSizes" :key="sz.label"
                    @click="uploadForm.size = sz.label"
                    :class="['chip chip-size', uploadForm.size === sz.label ? 'active' : '']">
                    <span class="chip-sz-num">{{ sz.label }}</span>
                    <span class="chip-sz-note">{{ sz.note }}</span>
                  </button>
                </div>

                <!-- Custom size toggle -->
                <div style="margin-top:10px">
                  <button @click="uploadForm.size = 'custom'"
                    :class="['chip', uploadForm.size === 'custom' ? 'active' : '']">
                    ✏️ Custom size
                  </button>
                </div>
                <div v-if="uploadForm.size === 'custom'" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
                  <div class="form-row" style="margin:0">
                    <label class="form-label">Width (px)</label>
                    <input v-model.number="uploadForm.customW" type="number" class="input" placeholder="e.g. 640" />
                  </div>
                  <div class="form-row" style="margin:0">
                    <label class="form-label">Height (px)</label>
                    <input v-model.number="uploadForm.customH" type="number" class="input" placeholder="e.g. 100" />
                  </div>
                </div>
              </div>

              <!-- Weight limit indicator -->
              <div class="weight-indicator">
                <div class="weight-label">Max file size for <strong>{{ uploadForm.bannerType?.toUpperCase() || 'STATIC' }}</strong> {{ currentSizeLabel }}</div>
                <div class="weight-bar-wrap">
                  <div class="weight-bar" :style="{ width: weightPercent + '%', background: weightColor }"/>
                </div>
                <div class="weight-nums">
                  <span :style="{ color: weightColor }">{{ currentFileKB }} KB</span>
                  <span style="color:#555">/ {{ activeMaxKB }} KB max</span>
                </div>
              </div>

              <!-- Drop zone -->
              <div class="form-section">
                <div class="form-section-title">
                  {{ bannerTypeLabel }} file
                  <span class="spec-hint">{{ bannerAcceptHint }}</span>
                </div>
                <div :class="['drop-zone', dragOver ? 'drag-over' : '', bannerFile ? 'has-file' : '']"
                  @dragover.prevent="dragOver=true"
                  @dragleave="dragOver=false"
                  @drop.prevent="onDrop($event,'banner')"
                  @click="$refs.bannerInput.click()">
                  <input ref="bannerInput" type="file" hidden
                    :accept="bannerAccept"
                    @change="onFilePick($event,'banner')" />
                  <template v-if="!bannerFile">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:#333"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    <div style="font-size:12px;color:#555;margin-top:6px">Drag & drop or click to browse</div>
                    <div style="font-size:10px;color:#333;margin-top:3px">{{ bannerAcceptHint }}</div>
                  </template>
                  <template v-else>
                    <img v-if="bannerPreviewUrl && uploadForm.bannerType !== 'html5'" :src="bannerPreviewUrl" style="max-height:80px;max-width:100%;border-radius:4px;" />
                    <div v-if="uploadForm.bannerType === 'html5'" class="html5-preview">
                      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="color:#f97316"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>
                      <span style="font-size:11px;color:#f97316;margin-top:4px">HTML5 Bundle (ZIP)</span>
                    </div>
                    <div style="font-size:11px;color:#888;margin-top:6px">{{ bannerFile.name }} · {{ fmtBytes(bannerFile.size) }}</div>
                    <button @click.stop="clearFile('banner')" class="btn btn-ghost btn-xs" style="margin-top:4px">Remove</button>
                  </template>
                </div>
              </div>

              <!-- HTML5 extra fields -->
              <template v-if="uploadForm.bannerType === 'html5'">
                <div class="form-row">
                  <label class="form-label">Entry point (index file inside ZIP)</label>
                  <input v-model="uploadForm.html5Entry" class="input" placeholder="index.html" />
                </div>
                <div class="form-row">
                  <label class="form-label">Backup static image (fallback for no-JS)
                    <span class="spec-hint">Same size · JPG/PNG · max 150 KB</span>
                  </label>
                  <div class="drop-zone drop-zone-sm" @click="$refs.fallbackInput.click()">
                    <input ref="fallbackInput" type="file" hidden accept="image/jpeg,image/png,image/webp"
                      @change="onFilePick($event,'fallback')" />
                    <span style="font-size:11px;color:#555">{{ fallbackFile ? fallbackFile.name : 'Upload fallback image' }}</span>
                  </div>
                </div>
                <div class="html5-rules">
                  <div class="html5-rule">📦 ZIP содержит index.html + все ассеты</div>
                  <div class="html5-rule">🚫 Без внешних запросов из HTML5 (no external calls)</div>
                  <div class="html5-rule">⏱ Анимация не более 30 сек, 3 зацикливания</div>
                  <div class="html5-rule">🔈 Звук только по клику пользователя</div>
                  <div class="html5-rule">📐 Canvas/display должны соответствовать размеру баннера</div>
                </div>
              </template>

              <!-- GIF extra info -->
              <div v-if="uploadForm.bannerType === 'gif'" class="html5-rules">
                <div class="html5-rule">⏱ Макс. длительность анимации: 30 сек</div>
                <div class="html5-rule">🔁 Макс. кол-во петель: 3</div>
                <div class="html5-rule">🚫 Контент не должен мигать чаще 3 Гц (epilepsy)</div>
                <div class="html5-rule">📐 Размер кадра = точный размер баннера</div>
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

// ─── Banner types ───
const bannerTypes = [
  { v:'static', l:'Static', icon:'🖼' },
  { v:'gif',    l:'GIF',    icon:'🎞' },
  { v:'html5',  l:'HTML5',  icon:'⚡' },
]

// ─── Weight limits matrix: size group × banner type (KB) ───
// Keys: 'standard' | 'large' | 'mobile' | 'interstitial'
const weightMatrix = {
  static: { standard:150, large:200, mobile:100, interstitial:150 },
  gif:    { standard:200, large:300, mobile:150, interstitial:200 },
  html5:  { standard:200, large:300, mobile:150, interstitial:200 },
}

// ─── Desktop sizes ───
const desktopSizes = [
  // IAB standard
  { label:'300×250',  note:'Medium Rectangle ⭐', group:'standard' },
  { label:'728×90',   note:'Leaderboard ⭐',       group:'large'    },
  { label:'160×600',  note:'Wide Skyscraper ⭐',   group:'standard' },
  { label:'300×600',  note:'Half Page ⭐',          group:'large'    },
  { label:'970×90',   note:'Super Banner',          group:'large'    },
  { label:'970×250',  note:'Billboard ⭐',          group:'large'    },
  { label:'468×60',   note:'Full Banner',           group:'standard' },
  { label:'234×60',   note:'Half Banner',           group:'standard' },
  { label:'120×600',  note:'Skyscraper',            group:'standard' },
  { label:'120×240',  note:'Vertical Banner',       group:'standard' },
  { label:'240×400',  note:'Vertical Rectangle ⭐', group:'standard' },
  { label:'250×250',  note:'Square Pop-Up',         group:'standard' },
  { label:'200×200',  note:'Small Square',          group:'standard' },
  { label:'336×280',  note:'Large Rectangle',       group:'standard' },
  { label:'580×400',  note:'Netboard',              group:'large'    },
  { label:'980×120',  note:'Panorama',              group:'large'    },
  { label:'930×180',  note:'Top Banner',            group:'large'    },
  { label:'750×300',  note:'Portrait',              group:'large'    },
  { label:'750×200',  note:'Portrait Slim',         group:'large'    },
  { label:'750×100',  note:'Portrait Strip',        group:'large'    },
]

// ─── Mobile sizes ───
const mobileSizes = [
  { label:'320×50',   note:'Mobile Banner ⭐',      group:'mobile'       },
  { label:'320×100',  note:'Large Mobile Banner ⭐', group:'mobile'       },
  { label:'300×50',   note:'Mobile Banner Alt',     group:'mobile'       },
  { label:'300×100',  note:'Mobile Rectangle',      group:'mobile'       },
  { label:'320×480',  note:'Mobile Interstitial ⭐', group:'interstitial' },
  { label:'480×320',  note:'Mobile Landscape Int.', group:'interstitial' },
  { label:'360×640',  note:'Mobile Full Screen',    group:'interstitial' },
  { label:'375×667',  note:'iPhone Portrait',       group:'interstitial' },
  { label:'414×896',  note:'iPhone XR/11',          group:'interstitial' },
  { label:'390×844',  note:'iPhone 14',             group:'interstitial' },
  { label:'768×1024', note:'iPad Portrait',         group:'interstitial' },
  { label:'1024×768', note:'iPad Landscape',        group:'large'        },
  { label:'728×90',   note:'Tablet Leaderboard',    group:'large'        },
  { label:'300×250',  note:'In-app Rectangle ⭐',   group:'standard'     },
]

// ─── Special / Rich Media ───
const specialSizes = [
  { label:'1200×628',  note:'Social Feed / OG ⭐',  group:'large'    },
  { label:'600×500',   note:'Rich Media',           group:'large'    },
  { label:'1000×300',  note:'Filmstrip',            group:'large'    },
  { label:'800×250',   note:'Pushdown (expanded)',  group:'large'    },
  { label:'950×90',    note:'Pushdown (collapsed)', group:'large'    },
  { label:'300×1050',  note:'Portrait (tall)',      group:'large'    },
  { label:'320×320',   note:'Square Mobile',        group:'mobile'   },
  { label:'250×360',   note:'Ticket',               group:'standard' },
  { label:'180×150',   note:'Rectangle',            group:'standard' },
  { label:'125×125',   note:'Button (square)',      group:'standard' },
  { label:'120×90',    note:'Button 1',             group:'standard' },
  { label:'120×60',    note:'Button 2',             group:'standard' },
  { label:'88×31',     note:'Microbar',             group:'standard' },
]

// All sizes flat
const allBannerSizes = [...desktopSizes, ...mobileSizes, ...specialSizes]

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
const fallbackFile    = ref(null)

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
    size: '300×250', bannerType: 'static',
    customW: null, customH: null, html5Entry: 'index.html',
    clickUrl: '',
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

// ─────────── Computed helpers ───────────
const currentSizeObj = computed(() =>
  allBannerSizes.find(s => s.label === uploadForm.value.size) || null
)
const currentSizeLabel = computed(() =>
  uploadForm.value.size === 'custom'
    ? `${uploadForm.value.customW||'?'}×${uploadForm.value.customH||'?'}`
    : uploadForm.value.size
)

const activeMaxKB = computed(() => {
  const type  = uploadForm.value.bannerType || 'static'
  const group = currentSizeObj.value?.group || 'standard'
  return weightMatrix[type]?.[group] || 150
})

const currentFileKB = computed(() =>
  bannerFile.value ? Math.round(bannerFile.value.size / 1024) : 0
)
const weightPercent = computed(() =>
  Math.min(100, Math.round(currentFileKB.value / activeMaxKB.value * 100))
)
const weightColor = computed(() => {
  const p = weightPercent.value
  if (p > 90) return '#f87171'
  if (p > 70) return '#fbbf24'
  return '#34d399'
})

const bannerTypeHint = computed(() => ({
  static: 'JPG, PNG, WebP — одиночный кадр, нет анимации',
  gif:    'Анимированный GIF — макс. 30 сек / 3 петли',
  html5:  'ZIP-архив с index.html + ассеты — интерактивный баннер',
}[uploadForm.value.bannerType] || ''))

const bannerTypeLabel = computed(() => ({
  static:'Static image', gif:'Animated GIF', html5:'HTML5 bundle (ZIP)',
}[uploadForm.value.bannerType] || 'Banner'))

const bannerAccept = computed(() => ({
  static:'image/jpeg,image/png,image/webp',
  gif:   'image/gif',
  html5: 'application/zip,application/x-zip-compressed,.zip',
}[uploadForm.value.bannerType] || 'image/*'))

const bannerAcceptHint = computed(() => ({
  static:`JPG, PNG, WebP · max ${activeMaxKB.value} KB`,
  gif:   `GIF · max ${activeMaxKB.value} KB · 30s/3 loops max`,
  html5: `ZIP (HTML5) · max ${activeMaxKB.value} KB + fallback JPG/PNG`,
}[uploadForm.value.bannerType] || ''))

// ─────────── Computed: filter ───────────
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
    // Size
    const sizeOk = uploadForm.value.size && uploadForm.value.size !== 'custom' ||
      (uploadForm.value.size === 'custom' && uploadForm.value.customW > 0 && uploadForm.value.customH > 0)
    rules.push({ id:'bsize', label:'Banner size selected',
      status: sizeOk ? 'ok' : 'err',
      desc: sizeOk ? currentSizeLabel.value : 'Select or enter a size' })

    // Banner type
    rules.push({ id:'btype', label:'Creative type',
      status: 'ok',
      desc: { static:'Static image (JPG/PNG/WebP)', gif:'Animated GIF', html5:'HTML5 interactive (ZIP)' }[uploadForm.value.bannerType] || '-' })

    // File
    if (bannerFile.value) {
      const kb = bannerFile.value.size / 1024
      const maxKB = activeMaxKB.value

      rules.push({ id:'bweight', label:'File weight',
        status: kb <= maxKB ? 'ok' : 'err',
        desc: `${kb.toFixed(0)} KB / ${maxKB} KB max (${uploadForm.value.bannerType?.toUpperCase()})` })

      // Dimensions (only for static/gif)
      if (['static','gif'].includes(uploadForm.value.bannerType) && bannerImageDims.value && uploadForm.value.size !== 'custom') {
        const [sw,sh] = (uploadForm.value.size || '').split('×').map(Number)
        const dimOk = bannerImageDims.value.w === sw && bannerImageDims.value.h === sh
        rules.push({ id:'bdims', label:'Image dimensions',
          status: dimOk ? 'ok' : 'err',
          desc: dimOk
            ? `${sw}×${sh}px ✓`
            : `Got ${bannerImageDims.value.w}×${bannerImageDims.value.h} — expected ${sw}×${sh}` })
      }

      // Format compliance
      const ext = bannerFile.value.name.split('.').pop().toLowerCase()
      const expectedExts = { static:['jpg','jpeg','png','webp'], gif:['gif'], html5:['zip'] }
      const allowed = expectedExts[uploadForm.value.bannerType] || []
      rules.push({ id:'bformat', label:'File format',
        status: allowed.includes(ext) ? 'ok' : 'err',
        desc: `${ext.toUpperCase()} — expected: ${allowed.join(', ').toUpperCase()}` })

      // GIF specific
      if (uploadForm.value.bannerType === 'gif') {
        rules.push({ id:'bgifloop', label:'GIF loop policy',
          status: 'warn',
          desc: 'Verify: max 30s duration, 3 loops, no flashing >3Hz' })
      }

      // HTML5 specific
      if (uploadForm.value.bannerType === 'html5') {
        rules.push({ id:'bhtml5entry', label:'Entry point',
          status: uploadForm.value.html5Entry ? 'ok' : 'err',
          desc: uploadForm.value.html5Entry || 'Specify index file inside ZIP' })
        rules.push({ id:'bfallback', label:'Fallback image',
          status: fallbackFile.value ? 'ok' : 'warn',
          desc: fallbackFile.value ? fallbackFile.value.name : 'Recommended for no-JS environments' })
      }
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
  const t = uploadForm.value.bannerType || 'static'
  if (f === 'banner') {
    if (t === 'html5') return [
      { k:'Format',      v:'ZIP (index.html + assets)' },
      { k:'Max weight',  v:`${activeMaxKB.value} KB total bundle` },
      { k:'Animation',   v:'Max 30s, 3 loops' },
      { k:'No ext. calls',v:'All assets must be in ZIP' },
      { k:'Audio',       v:'User-initiated only' },
      { k:'Fallback',    v:'JPG/PNG same size, max 150 KB' },
      { k:'Click URL',   v:'HTTPS required' },
    ]
    if (t === 'gif') return [
      { k:'Format',      v:'GIF89a animated' },
      { k:'Max weight',  v:`${activeMaxKB.value} KB` },
      { k:'Animation',   v:'Max 30s, max 3 loops' },
      { k:'Flashing',    v:'No flash >3Hz (epilepsy rules)' },
      { k:'Frame size',  v:'Exact banner dimensions' },
      { k:'Click URL',   v:'HTTPS required' },
    ]
    // static
    return [
      { k:'Formats',     v:'JPG, PNG, WebP' },
      { k:'Max weight',  v:`${activeMaxKB.value} KB` },
      { k:'No animation',v:'Single frame only' },
      { k:'Color space', v:'sRGB' },
      { k:'Click URL',   v:'HTTPS required' },
      { k:'Border',      v:'1px solid if bg is white' },
    ]
  }
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
functi