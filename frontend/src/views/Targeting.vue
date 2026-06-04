<template>
  <div class="page-wrap">

    <!-- Header -->
    <div class="page-header">
      <div>
        <h1 class="page-title">Targeting</h1>
        <p class="page-subtitle">Configure bid request filters — all signals come from SSP in real time</p>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <select v-model="selectedCampaign" class="input" style="min-width:200px">
          <option value="">— Select campaign —</option>
          <option value="1">Summer Sale 2026</option>
          <option value="2">Brand Awareness Q2</option>
          <option value="3">Retargeting – Cart Abandonment</option>
        </select>
        <button @click="saveTargeting" :disabled="saving || !selectedCampaign" class="btn btn-primary">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
          {{ saving ? 'Saving...' : 'Save Targeting' }}
        </button>
      </div>
    </div>

    <!-- SSP data-flow info banner -->
    <div class="info-banner">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      <span>
        Все таргеты работают на основе данных из <strong>OpenRTB Bid Request</strong> от SSP.
        Биддер получает запрос, извлекает поля (<code>geo.country</code>, <code>device.os</code>, <code>site.domain</code> и т.д.)
        и сравнивает с вашими правилами. Если условие не выполнено — ставка не делается (<strong>no-bid</strong>).
      </span>
    </div>

    <div class="targeting-layout">

      <!-- LEFT COL: targeting blocks -->
      <div class="targeting-col">

        <!-- ═══════════════ 1. GEO ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('geo')">
            <div class="t-block-title">
              <span class="t-icon">🌍</span>
              Geo Targeting
              <span class="t-badge" v-if="geoSummary">{{ geoSummary }}</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>geo.country / geo.region / geo.city</code></span>
              <svg :class="['chevron', open.geo ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>

          <div v-show="open.geo" class="t-block-body">
            <div class="t-hint">
              SSP передаёт <code>device.geo.country</code> (ISO 3166-1 alpha-2), <code>geo.region</code> (ISO 3166-2),
              <code>geo.city</code> (строка). Источник: IP-геолокация на стороне SSP (MaxMind или аналог).
              Биддер фильтрует до выставления ставки.
            </div>

            <div class="t-row">
              <label class="t-label">Mode</label>
              <div class="chip-group">
                <button v-for="m in ['whitelist','blacklist']" :key="m"
                  @click="geo.mode = m"
                  :class="['chip', geo.mode === m ? 'active' : '']">
                  {{ m === 'whitelist' ? '✅ Whitelist (разрешить)' : '🚫 Blacklist (запретить)' }}
                </button>
              </div>
            </div>

            <!-- Countries -->
            <div class="t-row">
              <label class="t-label">Countries</label>
              <div class="tag-input-wrap">
                <div class="tag-list">
                  <span v-for="c in geo.countries" :key="c" class="tag">
                    {{ countryFlag(c) }} {{ c }}
                    <button @click="removeGeo('countries', c)" class="tag-remove">×</button>
                  </span>
                </div>
                <div class="search-row">
                  <input v-model="geoSearch.country" @input="filterCountries" @keydown.enter="addTopCountry"
                    class="input input-sm" placeholder="Search country (e.g. RU, US, DE)..." />
                </div>
                <div v-if="geoSearch.countryResults.length" class="suggest-list">
                  <div v-for="c in geoSearch.countryResults" :key="c.code"
                    @click="addGeo('countries', c.code)"
                    class="suggest-item">
                    {{ countryFlag(c.code) }} <strong>{{ c.code }}</strong> — {{ c.name }}
                  </div>
                </div>
              </div>
            </div>

            <!-- Quick presets -->
            <div class="t-row">
              <label class="t-label">Quick presets</label>
              <div class="chip-group" style="flex-wrap:wrap">
                <button v-for="preset in geoPresets" :key="preset.label"
                  @click="applyGeoPreset(preset)"
                  class="chip chip-sm">
                  {{ preset.label }}
                </button>
              </div>
            </div>

            <!-- Regions (only if 1 country selected) -->
            <div v-if="geo.countries.length === 1" class="t-row">
              <label class="t-label">Regions / States</label>
              <textarea v-model="geo.regions" class="input textarea-sm"
                placeholder="Через запятую: Moscow, Saint Petersburg, Novosibirsk" />
              <div class="t-hint-sm">Совпадение с <code>geo.region</code> или <code>geo.city</code> (substring)</div>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 2. DEVICE ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('device')">
            <div class="t-block-title">
              <span class="t-icon">📱</span>
              Device Type
              <span class="t-badge" v-if="deviceTarget.types.filter(x=>x.on).length < deviceTarget.types.length">
                {{ deviceTarget.types.filter(x=>x.on).map(x=>x.l).join(', ') }}
              </span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>device.devicetype</code></span>
              <svg :class="['chevron', open.device ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.device" class="t-block-body">
            <div class="t-hint">
              OpenRTB поле <code>device.devicetype</code>: 1=Mobile/Tablet, 2=PC, 3=TV, 4=Phone,
              5=Tablet, 6=Connected Device, 7=Set Top Box. SSP всегда передаёт это поле.
            </div>
            <div class="device-grid">
              <label v-for="d in deviceTarget.types" :key="d.v"
                :class="['device-card', d.on ? 'on' : '']"
                @click="d.on = !d.on">
                <span class="device-icon">{{ d.icon }}</span>
                <span class="device-label">{{ d.l }}</span>
                <span class="device-rtb">IAB {{ d.v }}</span>
                <div class="device-check">
                  <svg v-if="d.on" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
                </div>
              </label>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 3. OS ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('os')">
            <div class="t-block-title">
              <span class="t-icon">⚙️</span>
              Operating System
              <span class="t-badge" v-if="osTarget.selected.length">{{ osTarget.selected.join(', ') }}</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>device.os / device.osv</code></span>
              <svg :class="['chevron', open.os ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.os" class="t-block-body">
            <div class="t-hint">
              <code>device.os</code> — строка ОС (Android, iOS, Windows, macOS, Linux).
              <code>device.osv</code> — версия (например "14.0"). Биддер делает case-insensitive сравнение.
            </div>
            <div class="t-row">
              <label class="t-label">Mode</label>
              <div class="chip-group">
                <button v-for="m in ['whitelist','blacklist']" :key="m"
                  @click="osTarget.mode = m"
                  :class="['chip', osTarget.mode === m ? 'active' : '']">
                  {{ m === 'whitelist' ? '✅ Whitelist' : '🚫 Blacklist' }}
                </button>
              </div>
            </div>
            <div class="os-grid">
              <label v-for="os in osList" :key="os.v"
                :class="['os-card', osTarget.selected.includes(os.v) ? 'on' : '']"
                @click="toggleList(osTarget.selected, os.v)">
                <span class="os-icon">{{ os.icon }}</span>
                <span class="os-label">{{ os.l }}</span>
              </label>
            </div>
            <!-- Version range -->
            <div v-if="osTarget.selected.length" class="t-row" style="margin-top:10px">
              <label class="t-label">Version range (optional)</label>
              <div style="display:flex;gap:8px;align-items:center">
                <input v-model="osTarget.minVer" class="input input-sm" placeholder="Min (e.g. 10)" style="width:120px"/>
                <span style="color:#555;font-size:11px">—</span>
                <input v-model="osTarget.maxVer" class="input input-sm" placeholder="Max (e.g. 17)" style="width:120px"/>
              </div>
              <div class="t-hint-sm">Фильтрует по <code>device.osv</code>. Пусто = любая версия.</div>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 4. BROWSER ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('browser')">
            <div class="t-block-title">
              <span class="t-icon">🌐</span>
              Browser
              <span class="t-badge" v-if="browserTarget.selected.length">{{ browserTarget.selected.join(', ') }}</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>device.ua</code> (User-Agent)</span>
              <svg :class="['chevron', open.browser ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.browser" class="t-block-body">
            <div class="t-hint">
              OpenRTB не имеет отдельного поля браузера. Биддер парсит <code>device.ua</code> (User-Agent string)
              через UA-parser (ua-parser2 / ua-parser-js) и определяет браузер автоматически.
              Это менее надёжно, чем гео или ОС — используй как дополнительный фильтр.
            </div>
            <div class="t-row">
              <label class="t-label">Mode</label>
              <div class="chip-group">
                <button v-for="m in ['whitelist','blacklist']" :key="m"
                  @click="browserTarget.mode = m"
                  :class="['chip', browserTarget.mode === m ? 'active' : '']">
                  {{ m === 'whitelist' ? '✅ Whitelist' : '🚫 Blacklist' }}
                </button>
              </div>
            </div>
            <div class="os-grid">
              <label v-for="b in browserList" :key="b.v"
                :class="['os-card', browserTarget.selected.includes(b.v) ? 'on' : '']"
                @click="toggleList(browserTarget.selected, b.v)">
                <span class="os-icon">{{ b.icon }}</span>
                <span class="os-label">{{ b.l }}</span>
              </label>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 5. CONNECTION ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('conn')">
            <div class="t-block-title">
              <span class="t-icon">📡</span>
              Connection Type
              <span class="t-badge" v-if="connTarget.selected.length < connList.length">
                {{ connTarget.selected.map(v => connList.find(c=>c.v===v)?.l).join(', ') }}
              </span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>device.connectiontype</code></span>
              <svg :class="['chevron', open.conn ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.conn" class="t-block-body">
            <div class="t-hint">
              <code>device.connectiontype</code> (IAB): 0=Unknown, 1=Ethernet, 2=WiFi, 3=Cellular (любая),
              4=2G, 5=3G, 6=4G/LTE, 7=5G. Важно для мобильной рекламы — пользователи на WiFi лучше конвертируют.
            </div>
            <div class="os-grid" style="grid-template-columns:repeat(4,1fr)">
              <label v-for="c in connList" :key="c.v"
                :class="['os-card', connTarget.selected.includes(c.v) ? 'on' : '']"
                @click="toggleList(connTarget.selected, c.v)">
                <span class="os-icon">{{ c.icon }}</span>
                <span class="os-label">{{ c.l }}</span>
                <span class="device-rtb">type {{ c.v }}</span>
              </label>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 6. TIME SCHEDULE ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('time')">
            <div class="t-block-title">
              <span class="t-icon">🕐</span>
              Day & Time Schedule
              <span class="t-badge" v-if="!allHoursOn">Dayparting active</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → timestamp (сервер биддера)</span>
              <svg :class="['chevron', open.time ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.time" class="t-block-body">
            <div class="t-hint">
              Время определяется на стороне биддера в момент получения запроса.
              Опционально: <code>geo.utcoffset</code> позволяет учесть часовой пояс пользователя.
              Клетки = часы в сутках. Зелёные = показываем, серые = не показываем.
            </div>
            <div class="t-row">
              <label class="t-label">Timezone</label>
              <div style="display:flex;gap:8px;align-items:center">
                <select v-model="timeTarget.tz" class="input input-sm" style="width:220px">
                  <option value="server">Серверное время (UTC)</option>
                  <option value="user">Время пользователя (geo.utcoffset)</option>
                  <option value="UTC+3">UTC+3 (Москва)</option>
                  <option value="UTC+0">UTC+0 (Лондон)</option>
                  <option value="UTC-5">UTC-5 (New York)</option>
                  <option value="UTC-8">UTC-8 (Los Angeles)</option>
                </select>
                <button @click="setAllHours(true)" class="btn btn-ghost btn-xs">Все часы</button>
                <button @click="setAllHours(false)" class="btn btn-ghost btn-xs">Очистить</button>
              </div>
            </div>

            <!-- Heatmap -->
            <div class="schedule-wrap">
              <div class="schedule-hours-label">
                <div v-for="h in 24" :key="h" class="hour-label">{{ h-1 }}</div>
              </div>
              <div v-for="day in weekDays" :key="day.v" class="schedule-row">
                <div class="day-label">{{ day.l }}</div>
                <div class="hour-cells">
                  <div v-for="h in 24" :key="h"
                    :class="['hour-cell', timeTarget.schedule[day.v]?.[h-1] ? 'on' : '']"
                    @mousedown="startPaint(day.v, h-1)"
                    @mouseenter="paintCell(day.v, h-1)"
                    @mouseup="stopPaint">
                  </div>
                </div>
              </div>
            </div>

            <!-- Quick presets -->
            <div class="chip-group" style="margin-top:10px;flex-wrap:wrap">
              <button v-for="p in timePresets" :key="p.label" @click="applyTimePreset(p)" class="chip chip-sm">
                {{ p.label }}
              </button>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 7. FREQUENCY CAP ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('freq')">
            <div class="t-block-title">
              <span class="t-icon">🔁</span>
              Frequency Cap
              <span class="t-badge" v-if="freq.enabled">{{ freq.count }}× / {{ freq.period }}</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>user.id / device.ifa / device.dpidsha1</code></span>
              <svg :class="['chevron', open.freq ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.freq" class="t-block-body">
            <div class="t-hint">
              Биддер хранит счётчик показов в Redis по ключу <code>freq:{campaign_id}:{user_id}</code>.
              Идентификатор пользователя берётся из: <code>user.id</code> (cookie/login),
              <code>device.ifa</code> (IDFA/GAID для мобайл) или <code>device.dpidsha1</code> (hashed device ID).
              При отсутствии ID — freq cap не применяется.
            </div>
            <div class="t-row">
              <label class="t-label">
                <input type="checkbox" v-model="freq.enabled" style="margin-right:6px"/> Enable Frequency Cap
              </label>
            </div>
            <template v-if="freq.enabled">
              <div class="t-row" style="display:flex;gap:12px;align-items:flex-end">
                <div>
                  <label class="t-label">Max impressions</label>
                  <input v-model.number="freq.count" type="number" min="1" class="input input-sm" style="width:100px"/>
                </div>
                <div>
                  <label class="t-label">Per period</label>
                  <select v-model="freq.period" class="input input-sm" style="width:140px">
                    <option value="hour">Per Hour</option>
                    <option value="day">Per Day</option>
                    <option value="week">Per Week</option>
                    <option value="campaign">Per Campaign (total)</option>
                  </select>
                </div>
              </div>
              <div class="t-row">
                <label class="t-label">TTL behaviour after cap reached</label>
                <div class="chip-group">
                  <button v-for="b in ['no-bid','use-next-creative']" :key="b"
                    @click="freq.behaviour = b"
                    :class="['chip chip-sm', freq.behaviour === b ? 'active' : '']">
                    {{ b === 'no-bid' ? '🚫 No-bid (skip)' : '🔄 Show next creative' }}
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- ═══════════════ 8. SITE / APP LISTS ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('sites')">
            <div class="t-block-title">
              <span class="t-icon">🌐</span>
              Site / App Targeting
              <span class="t-badge" v-if="sites.whitelist.length || sites.blacklist.length">
                {{ sites.whitelist.length }} allow · {{ sites.blacklist.length }} block
              </span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>site.domain / app.bundle</code></span>
              <svg :class="['chevron', open.sites ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.sites" class="t-block-body">
            <div class="t-hint">
              <code>site.domain</code> — домен сайта (example.com). <code>app.bundle</code> — bundle ID приложения
              (com.example.app для Android, 123456789 для iOS). Биддер проверяет whitelist до blacklist.
              Если whitelist не пуст — разрешены ТОЛЬКО сайты из него.
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
              <div>
                <div class="list-header">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                  Whitelist (разрешить только)
                </div>
                <textarea v-model="sitesWhitelistRaw" class="input textarea-sm"
                  placeholder="example.com&#10;news.ru&#10;com.android.app" style="height:120px"/>
                <div class="t-hint-sm">{{ sites.whitelist.length }} entries · по одному на строку</div>
              </div>
              <div>
                <div class="list-header" style="color:#f87171">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="2.5"><path d="M18 6 6 18M6 6l12 12"/></svg>
                  Blacklist (запретить)
                </div>
                <textarea v-model="sitesBlacklistRaw" class="input textarea-sm"
                  placeholder="badsite.com&#10;spam.net" style="height:120px"/>
                <div class="t-hint-sm">{{ sites.blacklist.length }} entries</div>
              </div>
            </div>
            <!-- IAB category filter -->
            <div class="t-row" style="margin-top:12px">
              <label class="t-label">IAB Content Categories (block)</label>
              <div class="chip-group" style="flex-wrap:wrap">
                <button v-for="cat in iabCategories" :key="cat.v"
                  @click="toggleList(sites.blockedCategories, cat.v)"
                  :class="['chip chip-sm', sites.blockedCategories.includes(cat.v) ? 'active-red' : '']">
                  {{ cat.l }}
                </button>
              </div>
              <div class="t-hint-sm">Поле <code>site.cat / app.cat</code> — массив IAB категорий сайта</div>
            </div>
          </div>
        </div>

        <!-- ═══════════════ 9. AUDIENCE SEGMENTS ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('audience')">
            <div class="t-block-title">
              <span class="t-icon">👥</span>
              Audience Segments
              <span class="t-badge" v-if="audience.selected.length">{{ audience.selected.length }} segments</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">bid.req → <code>user.data[].segment[].id</code></span>
              <svg :class="['chevron', open.audience ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.audience" class="t-block-body">
            <div class="t-hint">
              SSP может передавать аудиторные сегменты в <code>user.data[].segment[]</code>.
              Это данные DMP-платформы (Lotame, Salesforce DMP, etc.) или собственные 1st-party данные SSP.
              <strong>Не все SSP передают это поле.</strong> Для ретаргетинга используй Pixel Center (1st party).
            </div>
            <div class="t-row">
              <label class="t-label">Match logic</label>
              <div class="chip-group">
                <button v-for="m in ['ANY','ALL']" :key="m"
                  @click="audience.logic = m"
                  :class="['chip', audience.logic === m ? 'active' : '']">
                  {{ m === 'ANY' ? 'ANY (хотя бы 1 сегмент)' : 'ALL (все сегменты)' }}
                </button>
              </div>
            </div>
            <div class="audience-grid">
              <label v-for="seg in audienceSegments" :key="seg.id"
                :class="['audience-card', audience.selected.includes(seg.id) ? 'on' : '']"
                @click="toggleList(audience.selected, seg.id)">
                <div class="audience-title">{{ seg.name }}</div>
                <div class="audience-desc">{{ seg.desc }}</div>
                <div class="audience-src">{{ seg.source }}</div>
              </label>
            </div>

            <!-- Custom segment IDs -->
            <div class="t-row" style="margin-top:10px">
              <label class="t-label">Custom segment IDs (от вашего DMP)</label>
              <input v-model="audience.customIds" class="input input-sm"
                placeholder="seg_12345, seg_67890 (через запятую)" />
            </div>
          </div>
        </div>

        <!-- ═══════════════ 10. BID ADJUSTMENTS ═══════════════ -->
        <div class="t-block">
          <div class="t-block-header" @click="toggle('bid')">
            <div class="t-block-title">
              <span class="t-icon">💰</span>
              Bid Adjustments
              <span class="t-badge" v-if="bidAdj.rows.some(r=>r.mult!==1)">Active</span>
            </div>
            <div class="t-block-right">
              <span class="rtb-field">Множители к базовой ставке</span>
              <svg :class="['chevron', open.bid ? 'open' : '']" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
            </div>
          </div>
          <div v-show="open.bid" class="t-block-body">
            <div class="t-hint">
              Финальная ставка = <code>base_bid × Σ multipliers</code>.
              Например: базовая $1.00 × Мобайл(×1.2) × WiFi(×1.1) × Россия(×0.8) = <strong>$1.056</strong>.
              Множители перемножаются. 1.0 = без изменения.
            </div>
            <table class="adj-table">
              <thead>
                <tr>
                  <th>Dimension</th>
                  <th>Value</th>
                  <th>Multiplier</th>
                  <th>Result preview</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, i) in bidAdj.rows" :key="i">
                  <td>
                    <select v-model="row.dim" class="input input-sm">
                      <option v-for="d in adjDimensions" :key="d.v" :value="d.v">{{ d.l }}</option>
                    </select>
                  </td>
                  <td>
                    <input v-model="row.val" class="input input-sm" :placeholder="adjPlaceholder(row.dim)" />
                  </td>
                  <td>
                    <div class="mult-wrap">
                      <input v-model.number="row.mult" type="number" step="0.05" min="0.1" max="5"
                        class="input input-sm" style="width:80px"/>
                      <span :class="['mult-badge', row.mult > 1 ? 'up' : row.mult < 1 ? 'down' : 'neutral']">
                        {{ row.mult > 1 ? '▲' : row.mult < 1 ? '▼' : '=' }}
                        {{ row.mult > 1 ? '+' : '' }}{{ ((row.mult - 1)*100).toFixed(0) }}%
                      </span>
                    </div>
                  </td>
                  <td class="adj-preview">
                    ${{ (bidAdj.baseBid * row.mult).toFixed(3) }}
                  </td>
                  <td>
                    <button @click="bidAdj.rows.splice(i,1)" class="btn btn-ghost btn-xs" style="color:#f87171">×</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div style="display:flex;gap:10px;margin-top:10px;align-items:center">
              <button @click="addAdjRow" class="btn btn-ghost btn-xs">+ Add rule</button>
              <label class="t-label" style="margin:0">Base bid: $</label>
              <input v-model.number="bidAdj.baseBid" type="number" step="0.01" class="input input-sm" style="width:80px"/>
              <span style="font-size:11px;color:#555">
                Effective bid: <strong style="color:#34d399">${{ effectiveBid }}</strong>
              </span>
            </div>
          </div>
        </div>

      </div><!-- /left col -->

      <!-- RIGHT COL: summary + OpenRTB flow -->
      <div class="summary-col">

        <!-- Live summary -->
        <div class="summary-card">
          <div class="summary-title">Targeting Summary</div>

          <div class="summary-row">
            <span class="sum-label">Geo</span>
            <span class="sum-val">{{ geoSummary || 'All countries' }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Devices</span>
            <span class="sum-val">{{ deviceSummary }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">OS</span>
            <span class="sum-val">{{ osTarget.selected.length ? osTarget.selected.join(', ') : 'All' }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Browser</span>
            <span class="sum-val">{{ browserTarget.selected.length ? browserTarget.selected.join(', ') : 'All' }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Connection</span>
            <span class="sum-val">{{ connSummary }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Schedule</span>
            <span class="sum-val">{{ allHoursOn ? '24/7' : 'Custom dayparting' }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Freq Cap</span>
            <span class="sum-val">{{ freq.enabled ? `${freq.count}× / ${freq.period}` : 'Off' }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Sites W/B</span>
            <span class="sum-val">{{ sites.whitelist.length }} allow / {{ sites.blacklist.length }} block</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Audiences</span>
            <span class="sum-val">{{ audience.selected.length ? `${audience.selected.length} segments` : 'All' }}</span>
          </div>
          <div class="summary-row">
            <span class="sum-label">Bid adjustments</span>
            <span class="sum-val">{{ bidAdj.rows.filter(r=>r.mult!==1).length }} active</span>
          </div>
          <div class="summary-row" style="border-top:1px solid rgba(255,255,255,0.06);margin-top:8px;padding-top:8px">
            <span class="sum-label">Effective bid</span>
            <span class="sum-val" style="color:#34d399;font-weight:600">${{ effectiveBid }} CPM</span>
          </div>
        </div>

        <!-- OpenRTB flow diagram -->
        <div class="flow-card">
          <div class="summary-title">OpenRTB Flow — как работает таргетинг</div>
          <div class="flow-steps">
            <div class="flow-step">
              <div class="flow-num">1</div>
              <div class="flow-content">
                <div class="flow-title">SSP → Bid Request</div>
                <div class="flow-desc">SSP присылает JSON с данными об импрешне: <code>site.domain</code>, <code>device.ua</code>, <code>device.geo</code>, <code>user.id</code> и т.д. Время — до 100ms на ответ.</div>
              </div>
            </div>
            <div class="flow-arrow">↓</div>
            <div class="flow-step">
              <div class="flow-num">2</div>
              <div class="flow-content">
                <div class="flow-title">Bidder — фильтрация</div>
                <div class="flow-desc">Биддер (RTB4FREE Java) проверяет каждое поле против твоих правил. Порядок: Geo → Device → OS → Browser → Conn → Time → Freq → Sites → Audience. Первый мисс → no-bid.</div>
              </div>
            </div>
            <div class="flow-arrow">↓</div>
            <div class="flow-step">
              <div class="flow-num">3</div>
              <div class="flow-content">
                <div class="flow-title">Расчёт ставки</div>
                <div class="flow-desc">base_bid × bid adjustments. Проверка bid floor (минимальная цена от SSP в <code>imp.bidfloor</code>). Если наша ставка ниже floor — no-bid.</div>
              </div>
            </div>
            <div class="flow-arrow">↓</div>
            <div class="flow-step">
              <div class="flow-num">4</div>
              <div class="flow-content">
                <div class="flow-title">Bid Response → SSP</div>
                <div class="flow-desc">Биддер отвечает JSON с ценой, creative ID, ad markup. SSP проводит аукцион. Победитель получает Win Notice с clearing price.</div>
              </div>
            </div>
            <div class="flow-arrow">↓</div>
            <div class="flow-step">
              <div class="flow-num">5</div>
              <div class="flow-content">
                <div class="flow-title">Win Notice → Stats</div>
                <div class="flow-desc">Win notice записывает реальную цену покупки. Freq cap счётчик в Redis +1. Pixel tracker фиксирует показ при загрузке рекламы.</div>
              </div>
            </div>
          </div>
        </div>

      </div><!-- /summary col -->
    </div><!-- /layout -->

    <!-- Save toast -->
    <Transition name="toast">
      <div v-if="toast.show" class="toast" :class="toast.type">
        {{ toast.msg }}
      </div>
    </Transition>

  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'

// ═══════════════════════════════════════════════
//  DATA CONSTANTS
// ═══════════════════════════════════════════════

const weekDays = [
  { v:'mon', l:'Mon' }, { v:'tue', l:'Tue' }, { v:'wed', l:'Wed' },
  { v:'thu', l:'Thu' }, { v:'fri', l:'Fri' }, { v:'sat', l:'Sat' },
  { v:'sun', l:'Sun' },
]

const osList = [
  { v:'Android', icon:'🤖', l:'Android' },
  { v:'iOS',     icon:'🍎', l:'iOS' },
  { v:'Windows', icon:'🪟', l:'Windows' },
  { v:'macOS',   icon:'🖥', l:'macOS' },
  { v:'Linux',   icon:'🐧', l:'Linux' },
  { v:'ChromeOS',icon:'🔵', l:'ChromeOS' },
]

const browserList = [
  { v:'Chrome',  icon:'🟡', l:'Chrome' },
  { v:'Safari',  icon:'🔵', l:'Safari' },
  { v:'Firefox', icon:'🦊', l:'Firefox' },
  { v:'Edge',    icon:'💠', l:'Edge' },
  { v:'Opera',   icon:'🔴', l:'Opera' },
  { v:'Samsung', icon:'📱', l:'Samsung Browser' },
  { v:'UC',      icon:'🟠', l:'UC Browser' },
  { v:'Yandex',  icon:'🔶', l:'Yandex Browser' },
]

const connList = [
  { v:0,  icon:'❓', l:'Unknown'  },
  { v:1,  icon:'🔌', l:'Ethernet' },
  { v:2,  icon:'📶', l:'WiFi'     },
  { v:3,  icon:'📡', l:'Cellular' },
  { v:4,  icon:'🐢', l:'2G'       },
  { v:5,  icon:'🚶', l:'3G'       },
  { v:6,  icon:'🚀', l:'4G/LTE'   },
  { v:7,  icon:'⚡', l:'5G'       },
]

const iabCategories = [
  { v:'IAB25',   l:'Non-Standard Content' },
  { v:'IAB25-3', l:'Pornography' },
  { v:'IAB25-4', l:'Profane' },
  { v:'IAB26',   l:'Illegal Content' },
  { v:'IAB7',    l:'Health & Fitness' },
  { v:'IAB14',   l:'Society (sensitive)' },
  { v:'IAB11-4', l:'Immigration' },
  { v:'IAB8',    l:'Food & Drink' },
]

const audienceSegments = [
  { id:'retarget-cart',  name:'Cart Abandoners',    desc:'Посетили корзину, не купили', source:'Pixel (1st party)' },
  { id:'retarget-pdp',   name:'Product Viewers',    desc:'Просмотрели карточку товара', source:'Pixel (1st party)' },
  { id:'lookalike-conv', name:'Lookalike Buyers',   desc:'Похожи на совершивших покупку', source:'DMP' },
  { id:'intend-auto',    name:'Auto Intenders',     desc:'Интерес к автомобилям', source:'SSP DMP' },
  { id:'intend-finance', name:'Finance Intenders',  desc:'Интерес к финансам', source:'SSP DMP' },
  { id:'intend-travel',  name:'Travel Intenders',   desc:'Интерес к путешествиям', source:'SSP DMP' },
  { id:'demo-m18-34',    name:'Male 18-34',         desc:'Мужчины 18-34 года', source:'SSP DMP' },
  { id:'demo-f25-45',    name:'Female 25-45',       desc:'Женщины 25-45 лет', source:'SSP DMP' },
]

const adjDimensions = [
  { v:'country',    l:'Country' },
  { v:'device',     l:'Device type' },
  { v:'os',         l:'OS' },
  { v:'connection', l:'Connection' },
  { v:'hour',       l:'Hour of day' },
  { v:'weekday',    l:'Day of week' },
  { v:'site',       l:'Site domain' },
  { v:'audience',   l:'Audience segment' },
]

const geoPresets = [
  { label:'🇷🇺 RU',   countries:['RU'] },
  { label:'🇷🇺+СНГ',  countries:['RU','UA','KZ','BY','UZ','AZ','AM','GE','MD','TJ','TM','KG'] },
  { label:'🇺🇸 US',   countries:['US'] },
  { label:'🌍 Tier-1',countries:['US','GB','CA','AU','DE','FR','NL','SE','NO','CH'] },
  { label:'🌍 EU',    countries:['DE','FR','NL','SE','NO','CH','AT','BE','DK','FI','IT','ES','PL'] },
  { label:'🌏 APAC',  countries:['JP','KR','SG','AU','IN','TH','MY','ID','PH','VN'] },
]

const timePresets = [
  { label:'Business hours (9-18)',  days:['mon','tue','wed','thu','fri'], hours: Array.from({length:9},(_, i)=>i+9) },
  { label:'Evenings (18-23)',       days:['mon','tue','wed','thu','fri','sat','sun'], hours:[18,19,20,21,22,23] },
  { label:'Weekends only',          days:['sat','sun'], hours: Array.from({length:24},(_,i)=>i) },
  { label:'Night (22-06)',          days:['mon','tue','wed','thu','fri','sat','sun'], hours:[22,23,0,1,2,3,4,5,6] },
  { label:'All 24/7',               days:['mon','tue','wed','thu','fri','sat','sun'], hours: Array.from({length:24},(_,i)=>i) },
]

// World countries (subset — top 50 + СНГ)
const countriesDb = [
  {code:'RU',name:'Russia'},{code:'US',name:'United States'},{code:'DE',name:'Germany'},
  {code:'GB',name:'United Kingdom'},{code:'FR',name:'France'},{code:'CN',name:'China'},
  {code:'IN',name:'India'},{code:'BR',name:'Brazil'},{code:'JP',name:'Japan'},
  {code:'CA',name:'Canada'},{code:'AU',name:'Australia'},{code:'IT',name:'Italy'},
  {code:'ES',name:'Spain'},{code:'MX',name:'Mexico'},{code:'KR',name:'South Korea'},
  {code:'NL',name:'Netherlands'},{code:'TR',name:'Turkey'},{code:'PL',name:'Poland'},
  {code:'SE',name:'Sweden'},{code:'NO',name:'Norway'},{code:'CH',name:'Switzerland'},
  {code:'AT',name:'Austria'},{code:'BE',name:'Belgium'},{code:'DK',name:'Denmark'},
  {code:'FI',name:'Finland'},{code:'UA',name:'Ukraine'},{code:'KZ',name:'Kazakhstan'},
  {code:'BY',name:'Belarus'},{code:'UZ',name:'Uzbekistan'},{code:'AZ',name:'Azerbaijan'},
  {code:'AM',name:'Armenia'},{code:'GE',name:'Georgia'},{code:'MD',name:'Moldova'},
  {code:'TJ',name:'Tajikistan'},{code:'TM',name:'Turkmenistan'},{code:'KG',name:'Kyrgyzstan'},
  {code:'SG',name:'Singapore'},{code:'TH',name:'Thailand'},{code:'MY',name:'Malaysia'},
  {code:'ID',name:'Indonesia'},{code:'PH',name:'Philippines'},{code:'VN',name:'Vietnam'},
  {code:'ZA',name:'South Africa'},{code:'NG',name:'Nigeria'},{code:'EG',name:'Egypt'},
  {code:'SA',name:'Saudi Arabia'},{code:'AE',name:'UAE'},{code:'IL',name:'Israel'},
  {code:'AR',name:'Argentina'},{code:'CL',name:'Chile'},{code:'CO',name:'Colombia'},
]

// ═══════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════

const selectedCampaign = ref('')
const saving = ref(false)

const open = ref({ geo:true, device:true, os:false, browser:false, conn:false, time:false, freq:false, sites:false, audience:false, bid:false })
function toggle(key) { open.value[key] = !open.value[key] }

// Geo
const geo = ref({ mode:'whitelist', countries:[], regions:'' })
const geoSearch = ref({ country:'', countryResults:[] })

function filterCountries() {
  const q = geoSearch.value.country.toLowerCase()
  geoSearch.value.countryResults = q.length < 1 ? [] :
    countriesDb.filter(c =>
      (c.code.toLowerCase().includes(q) || c.name.toLowerCase().includes(q)) &&
      !geo.value.countries.includes(c.code)
    ).slice(0, 6)
}
function addTopCountry() {
  if (geoSearch.value.countryResults.length) {
    addGeo('countries', geoSearch.value.countryResults[0].code)
  }
}
function addGeo(field, val) {
  if (!geo.value[field].includes(val)) geo.value[field].push(val)
  geoSearch.value.country = ''
  geoSearch.value.countryResults = []
}
function removeGeo(field, val) {
  geo.value[field] = geo.value[field].filter(x => x !== val)
}
function applyGeoPreset(preset) {
  geo.value.countries = [...new Set([...geo.value.countries, ...preset.countries])]
}
function countryFlag(code) {
  try {
    return String.fromCodePoint(...[...code.toUpperCase()].map(c => 0x1F1E6 - 65 + c.charCodeAt(0)))
  } catch { return '🏳' }
}

const geoSummary = computed(() => {
  if (!geo.value.countries.length) return ''
  const mode = geo.value.mode === 'blacklist' ? 'NOT ' : ''
  return mode + geo.value.countries.slice(0,4).join(', ') + (geo.value.countries.length > 4 ? ` +${geo.value.countries.length - 4}` : '')
})

// Device
const deviceTarget = ref({
  types: [
    { v:4, icon:'📱', l:'Phone',   on:true },
    { v:5, icon:'📲', l:'Tablet',  on:true },
    { v:2, icon:'🖥', l:'Desktop', on:true },
    { v:3, icon:'📺', l:'TV',      on:false },
    { v:6, icon:'🎮', l:'Connected Device', on:false },
    { v:7, icon:'📡', l:'Set-Top Box',      on:false },
  ]
})

const deviceSummary = computed(() => {
  const on = deviceTarget.value.types.filter(x => x.on)
  if (on.length === deviceTarget.value.types.length) return 'All'
  return on.map(x => x.l).join(', ') || 'None'
})

// OS
const osTarget = ref({ mode:'whitelist', selected:[], minVer:'', maxVer:'' })

// Browser
const browserTarget = ref({ mode:'whitelist', selected:[] })

// Connection
const connTarget = ref({ selected:[2,6,7] }) // WiFi + 4G + 5G by default

const connSummary = computed(() => {
  if (connTarget.value.selected.length === connList.length) return 'All'
  return connTarget.value.selected.map(v => connList.find(c=>c.v===v)?.l).filter(Boolean).join(', ') || 'None'
})

// Time schedule
function makeSchedule(val=true) {
  return Object.fromEntries(weekDays.map(d => [d.v, Array(24).fill(val)]))
}
const timeTarget = ref({ tz:'server', schedule: makeSchedule(true) })

const allHoursOn = computed(() =>
  weekDays.every(d => timeTarget.value.schedule[d.v]?.every(Boolean))
)

function setAllHours(val) {
  timeTarget.value.schedule = makeSchedule(val)
}

function applyTimePreset(preset) {
  const s = makeSchedule(false)
  for (const day of preset.days) {
    for (const h of preset.hours) {
      if (s[day]) s[day][h] = true
    }
  }
  timeTarget.value.schedule = s
}

// Paint cells
const painting = ref(false)
const paintValue = ref(true)

function startPaint(day, h) {
  painting.value = true
  paintValue.value = !timeTarget.value.schedule[day][h]
  timeTarget.value.schedule[day][h] = paintValue.value
}
function paintCell(day, h) {
  if (!painting.value) return
  timeTarget.value.schedule[day][h] = paintValue.value
}
function stopPaint() { painting.value = false }

// Freq cap
const freq = ref({ enabled:false, count:3, period:'day', behaviour:'no-bid' })

// Sites
const sitesWhitelistRaw = ref('')
const sitesBlacklistRaw = ref('')
const sites = ref({ whitelist:[], blacklist:[], blockedCategories:[] })

watch(sitesWhitelistRaw, v => { sites.value.whitelist = v.split('\n').map(s=>s.trim()).filter(Boolean) })
watch(sitesBlacklistRaw, v => { sites.value.blacklist = v.split('\n').map(s=>s.trim()).filter(Boolean) })

// Audience
const audience = ref({ selected:[], logic:'ANY', customIds:'' })

// Bid adjustments
const bidAdj = ref({
  baseBid: 1.00,
  rows: [
    { dim:'device',  val:'Phone',   mult:1.2 },
    { dim:'connection', val:'WiFi', mult:1.1 },
  ]
})

const effectiveBid = computed(() => {
  let bid = bidAdj.value.baseBid
  for (const row of bidAdj.value.rows) {
    if (row.mult && row.mult !== 1) bid *= row.mult
  }
  return bid.toFixed(3)
})

function addAdjRow() {
  bidAdj.value.rows.push({ dim:'country', val:'', mult:1.0 })
}

function adjPlaceholder(dim) {
  return { country:'RU', device:'Phone', os:'Android', connection:'WiFi',
    hour:'18', weekday:'sat', site:'example.com', audience:'seg_123' }[dim] || '...'
}

// Shared helper
function toggleList(arr, val) {
  const i = arr.indexOf(val)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(val)
}

// Toast
const toast = ref({ show:false, msg:'', type:'ok' })
function showToast(msg, type='ok') {
  toast.value = { show:true, msg, type }
  setTimeout(() => { toast.value.show = false }, 2500)
}

async function saveTargeting() {
  if (!selectedCampaign.value) return
  saving.value = true
  try {
    const payload = {
      campaign_id: selectedCampaign.value,
      geo: { mode: geo.value.mode, countries: geo.value.countries, regions: geo.value.regions },
      devices: deviceTarget.value.types.filter(x=>x.on).map(x=>x.v),
      os: { mode: osTarget.value.mode, list: osTarget.value.selected, min_ver: osTarget.value.minVer, max_ver: osTarget.value.maxVer },
      browsers: { mode: browserTarget.value.mode, list: browserTarget.value.selected },
      connection_types: connTarget.value.selected,
      schedule: { tz: timeTarget.value.tz, hours: timeTarget.value.schedule },
      freq_cap: freq.value,
      sites: { whitelist: sites.value.whitelist, blacklist: sites.value.blacklist, blocked_cats: sites.value.blockedCategories },
      audience: { segments: audience.value.selected, logic: audience.value.logic, custom_ids: audience.value.customIds },
      bid_adjustments: bidAdj.value.rows,
      base_bid: bidAdj.value.baseBid,
    }
    await fetch(`/api/campaigns/${selectedCampaign.value}/targeting`, {
      method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)
    })
    showToast('✅ Targeting saved successfully')
  } catch { showToast('❌ Save failed — check connection', 'err') }
  finally { saving.value = false }
}
</script>

<style scoped>
/* ─── Layout ─── */
.targeting-layout {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) {
  .targeting-layout { grid-template-columns: 1fr; }
  .summary-col { display: none; }
}

.targeting-col { display: flex; flex-direction: column; gap: 8px; }
.summary-col   { display: flex; flex-direction: column; gap: 12px; position: sticky; top: 16px; }

/* ─── Info banner ─── */
.info-banner {
  display: flex; align-items: flex-start; gap: 8px;
  background: rgba(96,165,250,0.07); border: 1px solid rgba(96,165,250,0.15);
  border-radius: 10px; padding: 10px 14px; margin-bottom: 8px;
  font-size: 11px; color: #aaa; line-height: 1.6;
}
.info-banner code { background: rgba(255,255,255,0.08); border-radius: 3px; padding: 1px 4px; font-size: 10px; color: #60a5fa; }
.info-banner strong { color: #ddd; }

/* ─── Targeting blocks ─── */
.t-block {
  background: #0e0e0e; border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px; overflow: hidden;
}
.t-block-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; cursor: pointer; user-select: none;
  transition: background 150ms;
}
.t-block-header:hover { background: rgba(255,255,255,0.03); }
.t-block-title { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: #ddd; }
.t-icon { font-size: 14px; }
.t-badge { font-size: 10px; background: rgba(96,165,250,0.12); color: #60a5fa; border-radius: 4px; padding: 2px 6px; font-weight: 500; }
.t-block-right { display: flex; align-items: center; gap: 10px; }
.rtb-field { font-size: 10px; color: #444; font-family: monospace; }
.rtb-field code { color: #555; }
.chevron { transition: transform 200ms; color: #444; }
.chevron.open { transform: rotate(180deg); }
.t-block-body { padding: 12px 16px 16px; border-top: 1px solid rgba(255,255,255,0.05); display: flex; flex-direction: column; gap: 12px; }

/* ─── Form elements ─── */
.t-row { display: flex; flex-direction: column; gap: 5px; }
.t-label { font-size: 11px; color: #888; font-weight: 500; }
.t-hint { font-size: 10px; color: #555; line-height: 1.6; background: rgba(255,255,255,0.03); border-radius: 6px; padding: 8px 10px; }
.t-hint code { color: #60a5fa; background: rgba(96,165,250,0.1); border-radius: 3px; padding: 0 3px; }
.t-hint strong { color: #aaa; }
.t-hint-sm { font-size: 10px; color: #555; }
.t-hint-sm code { color: #60a5fa; }

.input-sm { padding: 5px 8px !important; font-size: 11px !important; height: 30px; }
.textarea-sm { min-height: 80px; resize: vertical; font-size: 11px; line-height: 1.5; }

/* ─── Tags ─── */
.tag-input-wrap { display: flex; flex-direction: column; gap: 6px; }
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; min-height: 24px; }
.tag {
  display: flex; align-items: center; gap: 4px;
  background: rgba(96,165,250,0.1); border: 1px solid rgba(96,165,250,0.2);
  border-radius: 5px; padding: 3px 7px; font-size: 11px; color: #93c5fd;
}
.tag-remove { background: none; border: none; color: #60a5fa; cursor: pointer; padding: 0; line-height: 1; font-size: 12px; }
.search-row { display: flex; gap: 6px; }
.suggest-list {
  background: #111; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
  overflow: hidden; max-height: 180px; overflow-y: auto;
}
.suggest-item {
  padding: 7px 12px; font-size: 11px; color: #ccc; cursor: pointer;
  transition: background 100ms;
}
.suggest-item:hover { background: rgba(255,255,255,0.05); }

/* ─── Device grid ─── */
.device-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 7px; }
.device-card {
  position: relative; display: flex; flex-direction: column; align-items: center;
  gap: 4px; padding: 10px 8px; border-radius: 10px; cursor: pointer;
  background: #111; border: 1px solid rgba(255,255,255,0.07);
  transition: all 150ms;
}
.device-card.on { background: rgba(96,165,250,0.1); border-color: rgba(96,165,250,0.3); }
.device-icon { font-size: 18px; }
.device-label { font-size: 11px; color: #ccc; }
.device-rtb { font-size: 9px; color: #444; }
.device-check {
  position: absolute; top: 5px; right: 5px;
  width: 14px; height: 14px; border-radius: 50%;
  background: rgba(96,165,250,0.2); display: flex; align-items: center; justify-content: center;
  color: #60a5fa;
}

/* ─── OS / Browser grid ─── */
.os-grid { display: grid; grid-template-columns: repeat(4,1fr); gap: 6px; }
.os-card {
  display: flex; flex-direction: column; align-items: center; gap: 3px;
  padding: 8px 4px; border-radius: 8px; cursor: pointer;
  background: #111; border: 1px solid rgba(255,255,255,0.07);
  transition: all 150ms;
}
.os-card.on { background: rgba(52,211,153,0.1); border-color: rgba(52,211,153,0.3); }
.os-icon { font-size: 16px; }
.os-label { font-size: 10px; color: #aaa; text-align: center; }

/* ─── Schedule ─── */
.schedule-wrap { display: flex; flex-direction: column; gap: 2px; }
.schedule-hours-label { display: flex; margin-left: 42px; gap: 0; }
.hour-label { width: 22px; font-size: 8px; color: #444; text-align: center; flex-shrink: 0; }
.schedule-row { display: flex; align-items: center; gap: 4px; }
.day-label { width: 38px; font-size: 10px; color: #666; flex-shrink: 0; }
.hour-cells { display: flex; gap: 1px; }
.hour-cell {
  width: 22px; height: 18px; border-radius: 3px; cursor: pointer;
  background: rgba(255,255,255,0.05); transition: background 80ms; flex-shrink: 0;
}
.hour-cell.on { background: rgba(52,211,153,0.5); }
.hour-cell:hover { background: rgba(52,211,153,0.3); }

/* ─── Site lists ─── */
.list-header { display: flex; align-items: center; gap: 5px; font-size: 11px; color: #34d399; margin-bottom: 6px; font-weight: 600; }

/* ─── IAB category chip active-red ─── */
.chip.active-red { background: rgba(248,113,113,0.15); border-color: rgba(248,113,113,0.3); color: #f87171; }

/* ─── Audience ─── */
.audience-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.audience-card {
  display: flex; flex-direction: column; gap: 2px; padding: 9px 10px;
  border-radius: 8px; cursor: pointer;
  background: #111; border: 1px solid rgba(255,255,255,0.07);
  transition: all 150ms;
}
.audience-card.on { background: rgba(167,139,250,0.1); border-color: rgba(167,139,250,0.3); }
.audience-title { font-size: 11px; color: #ddd; font-weight: 600; }
.audience-desc  { font-size: 10px; color: #666; }
.audience-src   { font-size: 9px; color: #444; margin-top: 2px; }

/* ─── Bid adjustments ─── */
.adj-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.adj-table th { color: #555; font-weight: 500; padding: 4px 6px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }
.adj-table td { padding: 5px 6px; border-bottom: 1px solid rgba(255,255,255,0.04); }
.mult-wrap { display: flex; align-items: center; gap: 6px; }
.mult-badge { font-size: 10px; font-weight: 600; padding: 2px 5px; border-radius: 4px; white-space: nowrap; }
.mult-badge.up   { background: rgba(52,211,153,0.12); color: #34d399; }
.mult-badge.down { background: rgba(248,113,113,0.12); color: #f87171; }
.mult-badge.neutral { background: rgba(255,255,255,0.05); color: #666; }
.adj-preview { color: #34d399; font-family: monospace; font-size: 11px; }

/* ─── Summary card ─── */
.summary-card {
  background: #0e0e0e; border: 1px solid rgba(255,255,255,0.07);
  border-radius: 12px; padding: 14px 16px;
}
.summary-title { font-size: 12px; font-weight: 700; color: #888; margin-bottom: 10px; letter-spacing: .5px; text-transform: uppercase; }
.summary-row { display: flex; justify-content: space-between; align-items: flex-start; padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.04); gap: 8px; }
.sum-label { font-size: 10px; color: #555; flex-shrink: 0; }
.sum-val   { font-size: 11px; color: #bbb; text-align: right; word-break: break-all; }

/* ─── Flow diagram ─── */
.flow-card {
  background: #0e0e0e; border: 1px solid rgba(255,255,255,0.07);
  border-radius: 12px; padding: 14px 16px;
}
.flow-steps { display: flex; flex-direction: column; gap: 0; }
.flow-step { display: flex; gap: 10px; padding: 8px 0; }
.flow-num {
  width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
  background: rgba(96,165,250,0.15); color: #60a5fa;
  display: flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700;
}
.flow-content { flex: 1; }
.flow-title { font-size: 11px; font-weight: 600; color: #ccc; margin-bottom: 3px; }
.flow-desc  { font-size: 10px; color: #555; line-height: 1.5; }
.flow-desc code { color: #60a5fa; font-size: 9px; }
.flow-desc strong { color: #888; }
.flow-arrow { color: #333; font-size: 16px; text-align: center; padding: 0 0 0 8px; }

/* ─── HTML5 rules ─── */
.html5-rules { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.html5-rule  { font-size: 10px; color: #666; padding: 3px 0; }

/* ─── Size group labels ─── */
.size-group-label { font-size: 10px; color: #555; font-weight: 600; margin-bottom: 4px; margin-top: 6px; letter-spacing: .3px; text-transform: uppercase; }

/* ─── Toast ─── */
.toast {
  position: fixed; bottom: 24px; right: 24px; z-index: 999;
  background: #1a1a1a; border-radius: 10px; padding: 10px 18px;
  font-size: 12px; color: #ddd; border: 1px solid rgba(255,255,255,0.1);
  box-shadow: 0 8px 24px rgba(0,0,0,0.4);
}
.toast.err { border-color: rgba(248,113,113,0.3); color: #f87171; }
.toast-enter-active, .toast-leave-active { transition: all 300ms; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(12px); }
</style>
