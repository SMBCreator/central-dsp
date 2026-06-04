package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * Campaign DTO deserialized from GET /api/rtb/campaigns.
 * Mirrors Python's RTBCampaignOut schema.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class CampaignDTO {

    @JsonProperty("id")
    private Long id;

    @JsonProperty("name")
    private String name;

    /** Campaign status: "active", "paused", "stopped", "archived". */
    @JsonProperty("status")
    private String status;

    /**
     * Base bid price in CPM (USD).
     * BidEngine uses this as the starting bid before adjustments.
     */
    @JsonProperty("bid_price")
    private Double bidPrice;

    @JsonProperty("bid_floor")
    private Double bidFloor;

    @JsonProperty("bid_ceiling")
    private Double bidCeiling;

    /** Total lifetime budget in USD. Null = no total budget limit. */
    @JsonProperty("total_budget")
    private Double totalBudget;

    /** Daily budget in USD. Null = no daily budget limit. */
    @JsonProperty("daily_budget")
    private Double dailyBudget;

    /**
     * Remaining budget (computed by backend).
     * Used for quick in-bidder budget checks.
     */
    @JsonProperty("budget_remaining")
    private Double budgetRemaining;

    /**
     * Default click-through URL for creatives that don't specify their own.
     * Used by AdMarkupService as fallback.
     */
    @JsonProperty("click_url")
    private String clickUrl;

    /**
     * 1x1 impression tracking pixel URL.
     * Injected into banner/native/VAST markup by AdMarkupService.
     */
    @JsonProperty("tracking_pixel")
    private String trackingPixel;

    /** Targeting rules. May be null if no targeting configured. */
    @JsonProperty("targeting")
    private TargetingDTO targeting;

    /** Active, approved creatives ready to serve. */
    @JsonProperty("creatives")
    private List<CreativeDTO> creatives;

    /**
     * Max frequency cap impressions per user per window.
     * 0 or null = no frequency cap.
     */
    @JsonProperty("freq_cap")
    private Integer freqCap;

    /**
     * Convenience: returns true if campaign is currently active
     * and has at least one servable creative.
     */
    public boolean isReadyToServe() {
        if (!"active".equals(status)) return false;
        if (creatives == null || creatives.isEmpty()) return false;
        return creatives.stream().anyMatch(CreativeDTO::isServable);
    }
}
