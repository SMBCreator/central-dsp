package io.centraldsp.bidder.controller;

import io.centraldsp.bidder.service.BudgetService;
import io.centraldsp.bidder.service.FrequencyCapService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * Win and Loss notification endpoints.
 *
 * <p>These are called by the SSP after auction settlement:
 * <ul>
 *   <li>GET /bidder/win  — called by SSP via NURL macro expansion
 *                          (${AUCTION_PRICE} replaced with clearing price).</li>
 *   <li>POST /bidder/loss — optional loss notice from SSP.</li>
 * </ul>
 * </p>
 *
 * <p>Two-phase win processing (server-side fanout):
 * <ol>
 *   <li>Handle in-bidder side-effects synchronously:
 *       deduct budget from Redis, increment frequency cap counter.</li>
 *   <li>Forward the win/loss notice asynchronously to the Python backend
 *       (GET /api/rtb/win, POST /api/rtb/loss-notice) so it can write
 *       AuctionLog + impression Event to PostgreSQL. Uses
 *       CompletableFuture.runAsync (ForkJoinPool) — the SSP already
 *       received 204 before the backend call starts.</li>
 * </ol>
 * </p>
 */
@Slf4j
@RestController
@RequestMapping("/bidder")
@RequiredArgsConstructor
public class WinLossController {

    private final BudgetService budgetService;
    private final FrequencyCapService frequencyCapService;

    /** Backend base URL, e.g. http://backend:8000 */
    @Value("${backend.url:http://backend:8000}")
    private String backendUrl;

    /**
     * Shared HTTP client — reused across calls (keep-alive connection pool).
     * java.net.http.HttpClient is thread-safe and connection-pooling.
     */
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .connectTimeout(Duration.ofMillis(500))
            .build();

    // ───────────────────────────────────────────────────────────
    // Win Notice
    // ───────────────────────────────────────────────────────────

    /**
     * Win notice — fired by SSP after ${AUCTION_PRICE} macro expansion.
     *
     * <p>URL pattern (built in BidEngineService):
     * {@code GET /bidder/win?campaign_id={id}&creative_id={id}&auction_id={id}&imp_id={id}&win_price={price}&user_id={uid}}</p>
     */
    @GetMapping("/win")
    public ResponseEntity<Void> winNoticeGet(
            @RequestParam("campaign_id")                             Long   campaignId,
            @RequestParam(value = "win_price",   defaultValue = "0") double winPrice,
            @RequestParam(value = "creative_id", required = false)   Long   creativeId,
            @RequestParam(value = "auction_id",  required = false)   String auctionId,
            @RequestParam(value = "imp_id",       required = false)   String impId,
            @RequestParam(value = "user_id",      required = false)   String userId
    ) {
        handleWin(campaignId, winPrice, userId, auctionId);
        forwardWinToBackendAsync(auctionId, impId, campaignId, creativeId, winPrice);
        return ResponseEntity.noContent().build();
    }

    /** Win notice — POST variant (some SSPs use POST). */
    @PostMapping("/win")
    public ResponseEntity<Void> winNoticePost(@RequestBody Map<String, Object> body) {
        Long   campaignId = parseLong(body, "campaign_id");
        Long   creativeId = parseLong(body, "creative_id");
        double winPrice   = parseDouble(body, "win_price");
        String userId     = parseString(body, "user_id");
        String auctionId  = parseString(body, "auction_id");
        String impId      = parseString(body, "imp_id");

        if (campaignId != null) {
            handleWin(campaignId, winPrice, userId, auctionId);
            forwardWinToBackendAsync(auctionId, impId, campaignId, creativeId, winPrice);
        }
        return ResponseEntity.noContent().build();
    }

    // ───────────────────────────────────────────────────────────
    // Loss Notice
    // ───────────────────────────────────────────────────────────

    /** Loss notice — GET variant. */
    @GetMapping("/loss")
    public ResponseEntity<Void> lossNoticeGet(
            @RequestParam(value = "campaign_id",  required = false) Long    campaignId,
            @RequestParam(value = "creative_id",  required = false) Long    creativeId,
            @RequestParam(value = "auction_id",   required = false) String  auctionId,
            @RequestParam(value = "imp_id",        required = false) String  impId,
            @RequestParam(value = "loss_reason",  required = false) Integer lossReason
    ) {
        log.debug("[LOSS] campaign={} auction={} reason={}", campaignId, auctionId, lossReason);
        forwardLossToBackendAsync(auctionId, impId, campaignId, creativeId, lossReason);
        return ResponseEntity.noContent().build();
    }

    /** Loss notice — POST variant. */
    @PostMapping("/loss")
    public ResponseEntity<Void> lossNoticePost(@RequestBody Map<String, Object> body) {
        Long    campaignId = parseLong(body, "campaign_id");
        Long    creativeId = parseLong(body, "creative_id");
        Integer lossReason = body.containsKey("loss_reason")
                ? Integer.parseInt(body.get("loss_reason").toString()) : null;
        String auctionId   = parseString(body, "auction_id");
        String impId       = parseString(body, "imp_id");

        log.debug("[LOSS POST] campaign={} auction={}", campaignId, auctionId);
        forwardLossToBackendAsync(auctionId, impId, campaignId, creativeId, lossReason);
        return ResponseEntity.noContent().build();
    }

    // ───────────────────────────────────────────────────────────
    // Internal helpers
    // ───────────────────────────────────────────────────────────

    /**
     * Synchronous in-bidder side effects: Redis budget deduction + freqcap.
     * Must complete before returning 204 to SSP.
     */
    private void handleWin(Long campaignId, double winPrice, String userId, String auctionId) {
        log.info("[WIN] campaign={} price={} auction={}", campaignId, winPrice, auctionId);
        // winPrice is CPM — per-impression cost = winPrice / 1000
        budgetService.deductBudget(campaignId, winPrice / 1000.0);
        if (userId != null && !userId.isBlank()) {
            frequencyCapService.recordImpression(campaignId, userId);
        }
    }

    /**
     * Async forward of win notice to Python backend.
     * Uses CompletableFuture (Java 11 compatible).
     * Best-effort: failures are logged but do not affect bidder operation.
     */
    private void forwardWinToBackendAsync(
            String auctionId, String impId,
            Long campaignId, Long creativeId, double winPrice) {

        if (auctionId == null || campaignId == null) {
            log.warn("[WIN-FWD] Skipping backend forward: missing auction_id or campaign_id");
            return;
        }

        final String url = backendUrl + "/api/rtb/win"
                + "?auction_id="  + enc(auctionId)
                + "&imp_id="      + enc(impId)
                + "&campaign_id=" + campaignId
                + "&creative_id=" + (creativeId != null ? creativeId : 0)
                + "&win_price="   + winPrice;

        CompletableFuture.runAsync(() -> {
            try {
                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(url))
                        .timeout(Duration.ofMillis(800))
                        .GET()
                        .build();
                HttpResponse<Void> resp = HTTP.send(req, HttpResponse.BodyHandlers.discarding());
                if (resp.statusCode() >= 400) {
                    log.warn("[WIN-FWD] Backend returned HTTP {} for auction={}",
                            resp.statusCode(), auctionId);
                }
            } catch (Exception e) {
                log.warn("[WIN-FWD] Failed to forward win to backend: auction={} err={}",
                        auctionId, e.getMessage());
            }
        });
    }

    /**
     * Async forward of loss notice to Python backend (POST).
     */
    private void forwardLossToBackendAsync(
            String auctionId, String impId,
            Long campaignId, Long creativeId, Integer lossReason) {

        if (auctionId == null || campaignId == null) return;

        final String url = backendUrl + "/api/rtb/loss-notice";
        final String body = String.format(
                "{\"auction_id\":\"%s\",\"imp_id\":\"%s\",\"campaign_id\":%d%s%s}",
                auctionId,
                impId != null ? impId : "",
                campaignId,
                creativeId != null ? ",\"creative_id\":" + creativeId : "",
                lossReason != null ? ",\"loss_reason\":" + lossReason : ""
        );

        CompletableFuture.runAsync(() -> {
            try {
                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(url))
                        .timeout(Duration.ofMillis(800))
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofString(body))
                        .build();
                HTTP.send(req, HttpResponse.BodyHandlers.discarding());
            } catch (Exception e) {
                log.warn("[LOSS-FWD] Failed to forward loss to backend: auction={} err={}",
                        auctionId, e.getMessage());
            }
        });
    }

    // ───────────────────────────────────────────────────────────
    // Parse helpers
    // ───────────────────────────────────────────────────────────

    private String enc(String s) {
        if (s == null) return "";
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    private Long parseLong(Map<String, Object> m, String key) {
        if (!m.containsKey(key)) return null;
        try { return Long.parseLong(m.get(key).toString()); }
        catch (Exception e) { return null; }
    }

    private double parseDouble(Map<String, Object> m, String key) {
        if (!m.containsKey(key)) return 0.0;
        try { return Double.parseDouble(m.get(key).toString()); }
        catch (Exception e) { return 0.0; }
    }

    private String parseString(Map<String, Object> m, String key) {
        if (!m.containsKey(key)) return null;
        Object v = m.get(key);
        return v != null ? v.toString() : null;
    }
}
