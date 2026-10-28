package rtb

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// Источники identity_key (план predeploy A.6). Bounded enum — label метрики
// bidder_identity_source_total.
const (
	IdentityBuyerUID  = "buyeruid"
	IdentityEIDs      = "eids"
	IdentityIFA       = "ifa"
	IdentityIDSync    = "idsync"
	IdentityUserID    = "user_id"
	IdentityAnonymous = "anonymous"
)

// IdentitySources — все значения label source (для метрик и тестов).
var IdentitySources = []string{IdentityBuyerUID, IdentityEIDs, IdentityIFA, IdentityIDSync, IdentityUserID, IdentityAnonymous}

const (
	idsyncPrefix = "idsync:"
	// idsyncLookupTimeout — жёсткий потолок единственного Redis-вызова ID sync
	// в hot path. Таймаут/ошибка -> следующий шаг цепочки, не no-bid.
	idsyncLookupTimeout = 5 * time.Millisecond
	maxIdentityLen      = 128
)

// validIdentity: непустой, не обнулённый, разумной длины, без управляющих символов.
func validIdentity(id string) bool {
	if isZeroID(id) || len(id) > maxIdentityLen {
		return false
	}
	for i := 0; i < len(id); i++ {
		if id[i] < 0x21 || id[i] == 0x7f {
			return false
		}
	}
	return true
}

// IDSyncKey — idsync:{partner}:{hex(sha256(salt || ssp_uid))}. Та же формула в
// backend/id_sync.py (hash_ssp_uid / redis_key); общий тест-вектор держит их в синхроне.
func IDSyncKey(partnerID, salt, sspUID string) string {
	sum := sha256.Sum256([]byte(salt + sspUID))
	return idsyncPrefix + partnerID + ":" + hex.EncodeToString(sum[:])
}

// syncLookupValue — ID пользователя у SSP, который партнёр передавал в /sync/in
// (id_source). buyeruid/ifa в sync не участвуют: buyeruid сопоставляет сам SSP.
func syncLookupValue(req *BidRequest, ident *feed.PartnerIdentity) string {
	if req.User == nil {
		return ""
	}
	src := ident.IDSource
	switch {
	case src == "" || src == "user_id":
		return req.User.ID
	case strings.HasPrefix(src, "eids:"):
		want := strings.TrimPrefix(src, "eids:")
		for _, eid := range req.User.EIDs {
			if eid.Source == want {
				for _, uid := range eid.UIDs {
					if validIdentity(uid.ID) {
						return uid.ID
					}
				}
			}
		}
	}
	return ""
}

// resolveIdentity — порядок идентификации A.6 (только при ID_SYNC_ENABLED;
// иначе — прежний deviceIDOf):
//  1. user.buyeruid — ID, уже сопоставленный SSP для нас (= наш dsp_uid);
//  2. user.eids[*] — по source из id_source (eids:<source>), иначе любой;
//  3. device.ifa — не нулевой;
//  4. Redis idsync:{partner}:{hash(ssp_uid)} -> dsp_uid (если у партнёра sync_enabled);
//  5. user.id в namespace партнёра p:{partner}:{user.id};
//  6. анонимный.
//
// Ключи 1 и 4 живут в одном пространстве dsp_uid: пользователь, сопоставленный
// через любой SSP, совпадает в аудиториях и frequency cap.
func (h *Handler) resolveIdentity(ctx context.Context, req *BidRequest, partner *feed.Partner) (key, source string) {
	ident := partner.Identity
	if req.User != nil {
		if validIdentity(req.User.BuyerUID) {
			return req.User.BuyerUID, IdentityBuyerUID
		}
		wantSource := ""
		if ident != nil && strings.HasPrefix(ident.IDSource, "eids:") {
			wantSource = strings.TrimPrefix(ident.IDSource, "eids:")
		}
		for _, eid := range req.User.EIDs {
			if wantSource != "" && eid.Source != wantSource {
				continue
			}
			for _, uid := range eid.UIDs {
				if validIdentity(uid.ID) {
					return uid.ID, IdentityEIDs
				}
			}
		}
	}
	if req.Device != nil && validIdentity(req.Device.IFA) {
		return req.Device.IFA, IdentityIFA
	}
	if ident != nil && ident.SyncEnabled && partner.IDSyncSalt != "" && h.redis != nil {
		if v := syncLookupValue(req, ident); validIdentity(v) {
			if dsp, ok := h.idsyncLookup(ctx, partner, v); ok {
				return dsp, IdentityIDSync
			}
		}
	}
	if req.User != nil && validIdentity(req.User.ID) {
		return "p:" + partner.ID + ":" + req.User.ID, IdentityUserID
	}
	return "", IdentityAnonymous
}

func (h *Handler) idsyncLookup(ctx context.Context, partner *feed.Partner, sspUID string) (string, bool) {
	lctx, cancel := context.WithTimeout(ctx, idsyncLookupTimeout)
	defer cancel()
	start := time.Now()
	v, err := h.redis.Raw().Get(lctx, IDSyncKey(partner.ID, partner.IDSyncSalt, sspUID)).Result()
	if h.metrics != nil {
		h.metrics.ObserveIDSyncLookup(time.Since(start).Seconds())
		if err != nil && !errors.Is(err, redis.Nil) {
			h.metrics.IncIDSyncLookupError()
		}
	}
	if err != nil || !validIdentity(v) {
		return "", false
	}
	return v, true
}
