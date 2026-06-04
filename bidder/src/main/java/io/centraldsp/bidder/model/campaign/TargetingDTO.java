package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * Targeting rules for a campaign — deserialized from /api/rtb/campaigns response.
 * Mirrors Python's TargetingOut schema.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class TargetingDTO {

    /** Minimum CPM bid floor. If SSP floor > this, SSP floor wins. */
    @JsonProperty("bid_floor")
    private Double bidFloor;

    /** Maximum CPM bid ceiling. Bids are clamped to this. */
    @JsonProperty("bid_ceiling")
    private Double bidCeiling;

    /**
     * Allowlisted ISO-3166-1 alpha-2 country codes (e.g. ["RU", "US"]).
     * Empty = allow all countries.
     */
    @JsonProperty("geo_countries")
    private List<String> geoCountries;

    /**
     * Allowed device types (DSP labels): "mobile", "desktop", "tablet", "ctv".
     * Empty = allow all device types.
     */
    @JsonProperty("device_types")
    private List<String> deviceTypes;

    /**
     * Allowed OS names (e.g. ["iOS", "Android", "Windows"]).
     * Empty = allow all operating systems.
     */
    @JsonProperty("os_list")
    private List<String> osList;

    /**
     * Allowed browser names (e.g. ["Chrome", "Safari"]).
     * Empty = allow all browsers.
     */
    @JsonProperty("browser_list")
    private List<String> browserList;

    /**
     * Site domain allowlist (e.g. ["news.ru", "sport.com"]).
     * If non-empty, only domains on this list are accepted.
     */
    @JsonProperty("site_whitelist")
    private List<String> siteWhitelist;

    /**
     * Site domain blocklist.
     * If non-empty, requests from these domains are rejected.
     */
    @JsonProperty("site_blacklist")
    private List<String> siteBlacklist;

    /**
     * Hour-of-week schedule for bid multipliers.
     * Format: {"0": {"9": 1.0, "10": 1.2}, "1": {...}}
     * Outer key = day-of-week (0=Mon..6=Sun), inner key = UTC hour, value = multiplier.
     * Empty = 24/7 uniform bidding.
     */
    @JsonProperty("hour_schedule")
    private Map<String, Map<String, Double>> hourSchedule;
}
