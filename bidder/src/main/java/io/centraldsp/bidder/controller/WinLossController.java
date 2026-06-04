package io.centraldsp.bidder.controller;

import io.centraldsp.bidder.service.BudgetService;
import io.centraldsp.bidder.service.FrequencyCapService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Win and Loss notification endpoints.
 *
 * <p>These are called by the SSP after auction settlement:
 * <ul>
 *   <li>GET /bidder/win  — called by the SSP via NURL macro expansion
 *                          when our bid wins. ${AUCTION_PRICE} is filled in.</li>
 *   <li>POST /bidder/loss — optional loss notice from SSP.</li>
 * </ul>
 * </p>
 *
 * <p>Responsibilities:
 * <ol>
 *   <li>Deduct actual clearing price from in-memory budget counters (Redis).</li>
 *   <li>Increment frequency cap counter for the user.</li>
 *   <li>The Python backend receives its own win-notice URL and records
 *       AuctionLog + impression Event there. This endpoint only handles
 *       the in-bidder side effects.</li>
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

    /**
     * Win notice — fired by SSP after ${AUCTION_PRICE} macro expansion.
     *
     * <p>URL pattern (built in BidEngineService):
     * GET /bidder/win?campaign_id={id}&win_price={price}&user_id={uid}</p>
     *
     * <p>Note: some SSPs fire this as GET with query params,
     * others POST a JSON body. We support both styles.</p>
     *
     * @param campaignId  campaign that won
     * @param winPrice    clearing price in CPM USD (SSP fills ${AUCTION_PRICE})
     * @param creativeId  creative that was served
     * @param auctionId   original BidRequest.id for correlation
     * @param userId      user ID for frequency cap increment
     */
    @GetMapping("/win")
    public ResponseEntity<Void> winNoticeGet(
            @RequestParam("campaign_id") Long campaignId,
            @RequestParam(value = "win_price", defaultValue = "0") double winPrice,
            @RequestParam(value = "creative_id", required = false) Long creativeId,
            @RequestParam(value = "auction_id", required = false) String auctionId,
            @RequestParam(value = "user_id", required = false) String userId
    ) {
        handleWin(campaignId, winPrice, userId, auctionId);
        return ResponseEntity.noContent().build();
    }

    /**
     * Win notice — POST variant (some SSPs use POST for win notices).
     */
    @PostMapping("/win")
    public ResponseEntity<Void> winNoticePost(@RequestBody Map<String, Object> body) {
        Long campaignId = body.containsKey("campaign_id")
                ? Long.parseLong(body.get("campaign_id").toString()) : null;
        double winPrice = body.containsKey("win_price")
                ? Double.parseDouble(body.get("win_price").toString()) : 0.0;
        String userId = body.containsKey("user_id")
                ? body.get("user_id").toString() : null;
        String auctionId = body.containsKey("auction_id")
                ? body.get("auction_id").toString() : null;

        if (campaignId != null) {
            handleWin(campaignId, winPrice, userId, auctionId);
        }
        return ResponseEntity.noContent().build();
    }

    /**
     * Loss notice — fired by SSP when we lost the auction.
     * Only used for stats; no budget/freqcap changes needed on loss.
     */
    @GetMapping("/loss")
    public ResponseEntity<Void> lossNoticeGet(
            @RequestParam(value = "campaign_id", required = false) Long campaignId,
            @RequestParam(value = "auction_id", required = false) String auctionId,
            @RequestParam(value = "loss_reason", required = false) Integer lossReason
    ) {
        log.debug("[LOSS] campaign={} auction={} reason={}", campaignId, auctionId, lossReason);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/loss")
    public ResponseEntity<Void> lossNoticePost(@RequestBody Map<String, Object> body) {
        log.debug("[LOSS POST] body={}", body);
        return ResponseEntity.noContent().build();
    }

    // ──────────────────────────────────────────────────────────────────

    private void handleWin(Long campaignId, double winPrice, String userId, String auctionId) {
        log.info("[WIN] campaign={} price={:.4f} auction={}", campaignId, winPrice, auctionId);

        // Deduct actual clearing price from Redis budget counter.
        // winPrice is CPM, convert to impression cost: winPrice / 1000.
        budgetService.deductBudget(campaignId, winPrice / 1000.0);

        // Increment frequency cap for this user.
        if (userId != null && !userId.isBlank()) {
            frequencyCapService.recordImpression(campaignId, userId);
        }
    }
}
