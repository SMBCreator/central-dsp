package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import io.centraldsp.bidder.model.campaign.CreativeDTO;
import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.BidResponse;
import io.centraldsp.bidder.model.ortb.Impression;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Core bid engine — the hot path.
 *
 * <p>For each Impression in a BidRequest:
 * <ol>
 *   <li>Iterate active campaigns (in-memory, O(N) per imp)</li>
 *   <li>Evaluate targeting (TargetingService)</li>
 *   <li>Check daily budget (BudgetService → Redis)</li>
 *   <li>Check frequency cap (FrequencyCapService → Redis)</li>
 *   <li>Pick highest bidding eligible campaign (internal first-price auction)</li>
 *   <li>Build Bid object with NURL and ad markup</li>
 * </ol>
 * </p>
 *
 * <p>Target latency: &lt;50ms total per BidRequest.</p>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class BidEngineService {

    private final CampaignLoaderService campaignLoader;
    private final TargetingService targetingService;
    private final BudgetService budgetService;
    private final FrequencyCapService frequencyCapService;
    private final AdMarkupService adMarkupService;

    /**
     * Win-notice base URL. Matches {@code bidder.win.url} in application.properties.
     * The SSP replaces ${AUCTION_PRICE} macro in the NURL before firing.
     *
     * <p>Default: http://bidder:8080/bidder/win (Docker Compose internal hostname).</p>
     */
    @Value("${bidder.win.url:http://bidder:8080/bidder/win}")
    private String winNotifyUrl;

    @Value("${auction.default.floor:0.01}")
    private double defaultFloor;

    /**
     * Process a BidRequest and return a BidResponse.
     * Returns empty seatbid (no bid) if no eligible campaigns found.
     */
    public BidResponse process(BidRequest bidRequest) {
        long start = System.currentTimeMillis();

        List<BidResponse.Bid> bids = new ArrayList<>();
        List<CampaignDTO> campaigns = campaignLoader.getActiveCampaigns();
        String userId = resolveUserId(bidRequest);

        for (Impression imp : bidRequest.getImp()) {
            BidResponse.Bid bestBid = null;
            double bestPrice = 0;

            for (CampaignDTO campaign : campaigns) {

                // 1. Targeting
                Optional<CreativeDTO> creativeOpt =
                        targetingService.match(campaign, bidRequest, imp);
                if (creativeOpt.isEmpty()) continue;

                // 2. Bid price
                double bidPrice = calculateBidPrice(campaign, imp);

                // 3. Budget check (bidPrice is CPM; divide by 1000 for per-imp cost)
                if (!budgetService.hasBudget(campaign, bidPrice / 1000.0)) continue;

                // 4. Frequency cap
                int freqCapLimit = (campaign.getFreqCap() != null && campaign.getFreqCap() > 0)
                        ? campaign.getFreqCap() : Integer.MAX_VALUE;
                if (!frequencyCapService.isWithinCap(campaign.getId(), userId, freqCapLimit)) continue;

                // 5. Internal auction: highest CPM wins
                if (bidPrice > bestPrice) {
                    bestPrice = bidPrice;
                    CreativeDTO creative = creativeOpt.get();

                    String nurl = buildWinNoticeUrl(
                            bidRequest.getId(), imp.getId(), campaign.getId(), creative.getId());
                    String adm = adMarkupService.buildMarkup(campaign, creative, bidRequest, imp);

                    bestBid = BidResponse.Bid.builder()
                            .id(UUID.randomUUID().toString())
                            .impid(imp.getId())
                            .price(bidPrice)
                            .nurl(nurl)
                            .crid(String.valueOf(creative.getId()))
                            .cid(String.valueOf(campaign.getId()))
                            .adm(adm)
                            .adomain(creative.getAdomain() != null
                                    ? List.of(creative.getAdomain()) : List.of())
                            .w(creative.getWidth())
                            .h(creative.getHeight())
                            .build();
                }
            }

            if (bestBid != null) {
                bids.add(bestBid);
            }
        }

        long elapsed = System.currentTimeMillis() - start;
        if (elapsed > 80) {
            log.warn("[BidEngine] Slow response: {}ms request={}", elapsed, bidRequest.getId());
        }

        if (bids.isEmpty()) {
            return BidResponse.builder()
                    .id(bidRequest.getId())
                    .build();
        }

        return BidResponse.builder()
                .id(bidRequest.getId())
                .bidid(UUID.randomUUID().toString())
                .cur("USD")
                .seatbid(List.of(
                        BidResponse.SeatBid.builder()
                                .seat("centraldsp")
                                .bid(bids)
                                .build()
                ))
                .build();
    }

    // ─────────────────────────────────────────────────────────────────────────

    /**
     * Calculate CPM bid price for a campaign/impression.
     *
     * <p>Logic:
     * <ol>
     *   <li>Start with campaign.bidPrice (base CPM)</li>
     *   <li>Ensure we meet the SSP bid floor</li>
     *   <li>Clamp between campaign's bid_floor and bid_ceiling</li>
     * </ol>
     * </p>
     */
    private double calculateBidPrice(CampaignDTO campaign, Impression imp) {
        double base = campaign.getBidPrice() != null ? campaign.getBidPrice() : defaultFloor;

        // Respect SSP bid floor
        double sspFloor = imp.getBidfloor() != null ? imp.getBidfloor() : 0.0;
        double effective = Math.max(base, sspFloor);

        // Clamp to campaign's bid floor/ceiling
        double floor   = campaign.getBidFloor()   != null ? campaign.getBidFloor()   : defaultFloor;
        double ceiling = campaign.getBidCeiling()  != null ? campaign.getBidCeiling() : base * 3;

        return Math.max(floor, Math.min(ceiling, effective));
    }

    /**
     * Build NURL with ${AUCTION_PRICE} macro.
     * SSP replaces ${AUCTION_PRICE} with the actual clearing price before firing.
     */
    private String buildWinNoticeUrl(String auctionId, String impId,
                                     Long campaignId, Long creativeId) {
        return winNotifyUrl
                + "?auction_id="  + encode(auctionId)
                + "&imp_id="      + encode(impId)
                + "&campaign_id=" + campaignId
                + "&creative_id=" + creativeId
                + "&win_price=${AUCTION_PRICE}";
    }

    private String resolveUserId(BidRequest request) {
        if (request.getUser() != null) {
            if (request.getUser().getBuyeruid() != null) return request.getUser().getBuyeruid();
            if (request.getUser().getId()       != null) return request.getUser().getId();
        }
        if (request.getDevice() != null && request.getDevice().getIfa() != null) {
            return request.getDevice().getIfa();
        }
        return null;
    }

    private String encode(String s) {
        if (s == null) return "";
        try { return URLEncoder.encode(s, StandardCharsets.UTF_8); }
        catch (Exception e) { return s; }
    }
}
