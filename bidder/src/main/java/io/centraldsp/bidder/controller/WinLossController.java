package io.centraldsp.bidder.controller;

import io.centraldsp.bidder.service.BudgetService;
import io.centraldsp.bidder.service.FrequencyCapService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Handles win and loss notifications from SSP.
 *
 * Win notice:  fired by SSP when our bid wins the auction.
 * Loss notice: fired by SSP when our bid loses (optional, not all SSPs support).
 */
@Slf4j
@RestController
@RequestMapping("/bidder")
@RequiredArgsConstructor
public class WinLossController {

    private final BudgetService budgetService;
    private final FrequencyCapService frequencyCapService;

    /**
     * Win notice — SSP calls this URL when we win.
     * URL built in BidEngineService.buildWinNoticeUrl().
     * SSP substitutes ${AUCTION_PRICE} with actual clearing price.
     */
    @GetMapping("/win")
    public ResponseEntity<Void> winNotice(
            @RequestParam("auction_id")  String auctionId,
            @RequestParam("imp_id")      String impId,
            @RequestParam("campaign_id") Long campaignId,
            @RequestParam("creative_id") Long creativeId,
            @RequestParam(value = "win_price", defaultValue = "0.0") Double winPrice,
            @RequestParam(value = "user_id", required = false) String userId
    ) {
        log.debug("[Win] auction={} campaign={} price={}", auctionId, campaignId, winPrice);

        // Record spend (CPM → dollars: price/1000)
        budgetService.recordSpend(campaignId, winPrice / 1000.0);

        // Increment frequency cap counter
        frequencyCapService.recordImpression(campaignId, userId);

        // HTTP 200 with empty body — SSP expects fast ack
        return ResponseEntity.ok().build();
    }

    /** Loss notice — SSP calls this when we lose. Log only. */
    @GetMapping("/loss")
    public ResponseEntity<Void> lossNotice(
            @RequestParam(value = "auction_id",  required = false) String auctionId,
            @RequestParam(value = "campaign_id", required = false) Long campaignId,
            @RequestParam(value = "loss_reason", required = false) Integer lossReason
    ) {
        log.debug("[Loss] auction={} campaign={} reason={}", auctionId, campaignId, lossReason);
        return ResponseEntity.ok().build();
    }
}
