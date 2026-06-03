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
 * Core bid engine.
 * For each impression in a BidRequest:
 *   1. Get all active campaigns
 *   2. Apply targeting (TargetingService)
 *   3. Check budget + frequency cap
 *   4. Select best creative
 *   5. Calculate bid price
 *   6. Build BidResponse
 *
 * Target latency: < 50ms total.
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

    @Value("${win.notify.url:http://backend:8000/api/rtb/win-notice}")
    private String winNotifyUrl;

    @Value("${loss.notify.url:http://backend:8000/api/rtb/loss-notice}")
    private String lossNotifyUrl;

    @Value("${auction.default.floor:0.01}")
    private double defaultFloor;

    @Value("${server.port:8080}")
    private int serverPort;

    /**
     * Process a BidRequest and return a BidResponse.
     * Returns no-bid (empty seatbid) if no eligible campaigns found.
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
                // Targeting check
                Optional<CreativeDTO> creativeOpt =
                        targetingService.match(campaign, bidRequest, imp);
                if (creativeOpt.isEmpty()) continue;

                // Budget check
                double bidPrice = calculateBidPrice(campaign, imp);
                if (!budgetService.hasBudget(campaign, bidPrice / 1000.0)) continue;
                // Note: bidPrice is CPM, budget is in total dollars; divide by 1000

                // Frequency cap check
                if (!frequencyCapService.canServe(campaign.getId(), userId)) continue;

                // Select highest bidder (internal auction)
                if (bidPrice > bestPrice) {
                    bestPrice = bidPrice;
                    CreativeDTO creative = creativeOpt.get();

                    String nurl = buildWinNoticeUrl(bidRequest.getId(), imp.getId(),
                            campaign.getId(), creative.getId());
                    String adm = adMarkupService.buildMarkup(campaign, creative,
                            bidRequest, imp);

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
            log.warn("[BidEngine] Slow response: {}ms for request {}", elapsed, bidRequest.getId());
        }

        if (bids.isEmpty()) {
            return BidResponse.builder()
                    .id(bidRequest.getId())
                    .build();  // no seatbid = no bid
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

    /**
     * Calculate bid price for a campaign/impression combo.
     * Formula: base bid * bid adjustments (from Redis/cache).
     * Clamped between bid_floor and bid_ceiling.
     */
    private double calculateBidPrice(CampaignDTO campaign, Impression imp) {
        double base = campaign.getBidPrice() != null ? campaign.getBidPrice() : defaultFloor;
        // Clamp
        double floor   = campaign.getBidFloor()   != null ? campaign.getBidFloor()   : defaultFloor;
        double ceiling = campaign.getBidCeiling()  != null ? campaign.getBidCeiling() : base * 3;
        // Also respect SSP floor
        double sspFloor = imp.getBidfloor() != null ? imp.getBidfloor() : 0.0;
        double effective = Math.max(base, sspFloor);
        return Math.max(floor, Math.min(ceiling, effective));
    }

    /**
     * Win notice URL with macros that SSP expands before firing.
     * ${AUCTION_PRICE} = actual clearing price (SSP fills this in).
     */
    private String buildWinNoticeUrl(String auctionId, String impId,
                                     Long campaignId, Long creativeId) {
        return winNotifyUrl
                + "?auction_id=" + encode(auctionId)
                + "&imp_id="     + encode(impId)
                + "&campaign_id=" + campaignId
                + "&creative_id=" + creativeId
                + "&win_price=${AUCTION_PRICE}";
    }

    private String resolveUserId(BidRequest request) {
        if (request.getUser() != null) {
            if (request.getUser().getBuyeruid() != null) return request.getUser().getBuyeruid();
            if (request.getUser().getId() != null)       return request.getUser().getId();
        }
        if (request.getDevice() != null && request.getDevice().getIfa() != null) {
            return request.getDevice().getIfa();
        }
        return null;
    }

    private String encode(String s) {
        try { return URLEncoder.encode(s, StandardCharsets.UTF_8); }
        catch (Exception e) { return s; }
    }
}
