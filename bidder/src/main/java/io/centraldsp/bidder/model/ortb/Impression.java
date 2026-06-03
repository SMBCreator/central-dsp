package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

/**
 * OpenRTB 2.5 Imp object — a single ad impression opportunity.
 */
@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class Impression {

    @JsonProperty("id")
    private String id;

    /** Banner ad opportunity. */
    @JsonProperty("banner")
    private Banner banner;

    /** Native ad opportunity. */
    @JsonProperty("native")
    private NativeAd nativeAd;

    /** Video ad opportunity. */
    @JsonProperty("video")
    private Video video;

    /** Minimum bid floor in CPM (USD). */
    @JsonProperty("bidfloor")
    private Double bidfloor;

    /** Currency of bid floor (default USD). */
    @JsonProperty("bidfloorcur")
    private String bidfloorcur;

    /** Secure flag: 1=HTTPS required. */
    @JsonProperty("secure")
    private Integer secure;
}
