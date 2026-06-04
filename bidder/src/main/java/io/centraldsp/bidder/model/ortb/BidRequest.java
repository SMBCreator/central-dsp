package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.1 — BidRequest top-level object.
 *
 * <p>All non-required fields are nullable — SSPs may omit them.
 * Use {@code @JsonIgnoreProperties(ignoreUnknown = true)} so unknown
 * extension fields ("ext") don't cause deserialization failures.</p>
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class BidRequest {

    /**
     * Unique ID of the bid request, provided by the exchange.
     * Required.
     */
    @JsonProperty("id")
    private String id;

    /**
     * Array of Impression objects representing the impressions offered.
     * At least 1 impression object is required.
     */
    @JsonProperty("imp")
    private List<Impression> imp;

    /** Details about the publisher's website. Mutually exclusive with app. */
    @JsonProperty("site")
    private Site site;

    /** Details about the publisher's app. Mutually exclusive with site. */
    @JsonProperty("app")
    private App app;

    /** Details about the user's device. */
    @JsonProperty("device")
    private Device device;

    /** Details about the human user of the device. */
    @JsonProperty("user")
    private User user;

    /**
     * Indicator of test mode (0=live, 1=test).
     * Bids on test mode must not be billable.
     */
    @JsonProperty("test")
    private Integer test;

    /**
     * Auction type (1=First Price, 2=Second Price Plus).
     * Default: 2.
     */
    @JsonProperty("at")
    private Integer at;

    /**
     * Maximum time in milliseconds to submit a bid to avoid timeout.
     * Default: platform-dependent but typically 100-150ms.
     */
    @JsonProperty("tmax")
    private Integer tmax;

    /**
     * Allowlist of buyer seats (DSP account IDs) allowed to bid.
     * If not present, no allowlist restriction.
     */
    @JsonProperty("wseat")
    private List<String> wseat;

    /**
     * Blocked list of buyer seats (DSP account IDs) not allowed to bid.
     */
    @JsonProperty("bseat")
    private List<String> bseat;

    /**
     * Array of blocked advertiser domains (e.g. "ford.com").
     */
    @JsonProperty("badv")
    private List<String> badv;

    /**
     * Array of IAB content categories that are blocked.
     */
    @JsonProperty("bcat")
    private List<String> bcat;

    /**
     * Blocked categories for overriding exchange quality / safety.
     * Requires bcat to be present.
     */
    @JsonProperty("cattax")
    private Integer cattax;

    /**
     * Array of languages for allowlisting (ISO 639-1 alpha-2).
     */
    @JsonProperty("wlang")
    private List<String> wlang;

    /**
     * Convenience: returns true if this is a test/sandbox request.
     */
    public boolean isTest() {
        return Integer.valueOf(1).equals(test);
    }

    /**
     * Convenience: returns site domain or app bundle for logging/targeting.
     */
    public String resolveInventoryId() {
        if (site != null && site.effectiveDomain() != null) return site.effectiveDomain();
        if (app != null && app.getBundle() != null)          return app.getBundle();
        return "unknown";
    }
}
