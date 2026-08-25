package io.centraldsp.bidder.service;

import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

/**
 * HMAC-signed event tokens, byte-compatible with backend/tokens.py:
 *
 *   v1.<bid_id>.<exp_unix>.<sig>
 *   sig = hex(HMAC-SHA256(secret, "v1|bid_id|exp"))[:32]
 *
 * Secret resolution mirrors Python: EVENT_TOKEN_SECRET, then SESSION_SECRET,
 * then the deterministic dev fallback.
 */
@Service
public class EventTokenService {

    private static final String VERSION = "v1";
    private static final long DEFAULT_TTL_SECONDS = 6 * 3600;

    private final byte[] secret;

    public EventTokenService() {
        String s = System.getenv("EVENT_TOKEN_SECRET");
        if (s == null || s.isEmpty()) s = System.getenv("SESSION_SECRET");
        if (s == null || s.isEmpty()) s = "centraldsp-dev-event-secret";
        this.secret = s.getBytes(StandardCharsets.UTF_8);
    }

    public String sign(String bidId) {
        long exp = Instant.now().getEpochSecond() + DEFAULT_TTL_SECONDS;
        return VERSION + "." + bidId + "." + exp + "." + sig(bidId, exp);
    }

    /** Package-visible for byte-compat tests. */
    String sig(String bidId, long exp) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            byte[] raw = mac.doFinal(
                    (VERSION + "|" + bidId + "|" + exp).getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(raw.length * 2);
            for (byte b : raw) hex.append(String.format("%02x", b));
            return hex.substring(0, 32);
        } catch (Exception e) {
            throw new IllegalStateException("HMAC unavailable", e);
        }
    }
}
