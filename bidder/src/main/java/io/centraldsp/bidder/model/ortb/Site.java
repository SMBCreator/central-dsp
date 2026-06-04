package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.13 — Site object.
 * Details about the publisher's website.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Site {

    /** Exchange-specific site ID. */
    @JsonProperty("id")
    private String id;

    /** Site name (may be masked at publisher's request). */
    @JsonProperty("name")
    private String name;

    /** Domain of the site (e.g., "pub.com"). */
    @JsonProperty("domain")
    private String domain;

    /** Array of IAB content categories of the site. */
    @JsonProperty("cat")
    private List<String> cat;

    /** Array of IAB content categories that describe the current page or view. */
    @JsonProperty("pagecat")
    private List<String> pagecat;

    /** URL of the page where the impression will be shown. */
    @JsonProperty("page")
    private String page;

    /** Referrer URL that caused navigation to the current page. */
    @JsonProperty("ref")
    private String ref;

    /** Search string that caused navigation to the current page. */
    @JsonProperty("search")
    private String search;

    /** Comma separated list of keywords about the site. */
    @JsonProperty("keywords")
    private String keywords;

    /** Details about the Publisher. */
    @JsonProperty("publisher")
    private Publisher publisher;

    /** Indicates if the site has a privacy policy (1=yes, 0=no). */
    @JsonProperty("privacypolicy")
    private Integer privacyPolicy;

    /**
     * Convenience: returns the effective domain for targeting.
     * Prefers domain field, falls back to extracting from page URL.
     */
    public String effectiveDomain() {
        if (domain != null && !domain.isBlank()) return domain.toLowerCase();
        if (page != null && !page.isBlank()) {
            try {
                java.net.URI uri = new java.net.URI(page);
                return uri.getHost() != null ? uri.getHost().toLowerCase() : null;
            } catch (Exception ignored) {}
        }
        return null;
    }
}
