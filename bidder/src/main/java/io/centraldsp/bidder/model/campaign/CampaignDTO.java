package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * Campaign DTO deserialized from GET /api/rtb/campaigns.
 * Mirrors Python's RTBCampaignOut schema.
 *
 * <p>CampaignLoaderService fetches this list from the backend
 * every {@code campaigns.update.interval} seconds and caches
 * it in memory for the bid engine.</p>
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

    /**
     * Minimum CPM bid (from TargetingDTO.bid_floor).
     * Kept at CampaignDTO level for quick access in BidEngine.
     */
    @JsonProperty("bid_floor")
    private Double bidFloor;

    /**
     * Maximum CPM bid (from TargetingDTO.bid_ceiling).
     */
    @JsonProperty("bid_ceiling")
    private Double bidCeiling;

    /** Total lifetime budget in USD. Null = no total budget limit. */
    @JsonProperty("total_budget")
    private Double totalBudget;

    /** Daily budget in USD. Null = no daily budget limit. */
    @JsonProperty("daily_budget")
    private Double dailyBudget;

    /**
     * Remaining budget in USD (computed by backend service).
     * Used by BudgetService for quick in-bidder budget checks.
     * Null = budget not limited or not yet computed.
     */
    @JsonProperty("budget_remaining")
    private Double budgetRemaining;

    /** Targeting rules for this campaign. May be null if no targeting configured. */
    @JsonProperty("targeting")
    private TargetingDTO targeting;

    /** Active, approved creatives ready to serve. */
    @JsonProperty("creatives")
    private List<CreativeDTO> creatives;

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
