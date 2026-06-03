package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import java.util.List;

/**
 * Campaign object as returned by Central DSP backend GET /api/rtb/campaigns.
 * Bidder uses this to make bid decisions.
 */
@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class CampaignDTO {

    @JsonProperty("id")
    private Long id;

    @JsonProperty("name")
    private String name;

    @JsonProperty("advertiser_id")
    private Long advertiserId;

    @JsonProperty("status")
    private String status;  // active, paused, exhausted

    // ── Budget ────────────────────────────────────────────────────────────────
    @JsonProperty("daily_budget")
    private Double dailyBudget;

    @JsonProperty("total_budget")
    private Double totalBudget;

    @JsonProperty("daily_spent")
    private Double dailySpent;

    @JsonProperty("total_spent")
    private Double totalSpent;

    // ── Bid Prices ────────────────────────────────────────────────────────────
    @JsonProperty("bid_floor")
    private Double bidFloor;    // minimum to participate

    @JsonProperty("bid_ceiling")
    private Double bidCeiling;  // maximum we ever bid

    @JsonProperty("bid_price")
    private Double bidPrice;    // default/base bid price in CPM USD

    // ── Targeting ─────────────────────────────────────────────────────────────
    @JsonProperty("geo_countries")
    private List<String> geoCountries;  // ISO 3166-1 alpha-2: ["US", "GB", "DE"]

    @JsonProperty("device_types")
    private List<Integer> deviceTypes;  // 1=desktop, 2=mobile, 3=tablet, 4=CTV

    @JsonProperty("os_list")
    private List<String> osList;  // ["android", "ios", "windows"]

    @JsonProperty("browser_list")
    private List<String> browserList;

    @JsonProperty("site_whitelist")
    private List<String> siteWhitelist;  // only these domains

    @JsonProperty("site_blacklist")
    private List<String> siteBlacklist;  // never these domains

    @JsonProperty("hour_schedule")
    private int[][] hourSchedule;  // [7][24] — true/false per day/hour

    // ── Creatives ─────────────────────────────────────────────────────────────
    @JsonProperty("creatives")
    private List<CreativeDTO> creatives;

    // ── Frequency Cap ─────────────────────────────────────────────────────────
    @JsonProperty("freq_cap_impressions")
    private Integer freqCapImpressions;  // max per user per day

    @JsonProperty("click_url")
    private String clickUrl;

    @JsonProperty("tracking_pixel")
    private String trackingPixel;
}
