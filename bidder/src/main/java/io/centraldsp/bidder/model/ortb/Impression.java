package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.4 — Impression object.
 * Describes the ad placement or impression being auctioned.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Impression {

    /** A unique identifier for this impression (within the bid request). */
    @JsonProperty("id")
    private String id;

    /** A Banner object (banner impression). */
    @JsonProperty("banner")
    private Banner banner;

    /** A Video object (video impression). */
    @JsonProperty("video")
    private Video video;

    /** A Native object (native impression). */
    @JsonProperty("native")
    private Native nativeObj;

    /**
     * Minimum bid for this impression expressed as CPM in ISO-4217 alpha codes.
     * Must be &gt;= 0.0. Default: 0.0.
     */
    @JsonProperty("bidfloor")
    private Double bidfloor;

    /** Currency specified using ISO-4217 alpha codes for bidfloor. */
    @JsonProperty("bidfloorcur")
    private String bidfloorcur;

    /**
     * Indicator of whether the impression requires secure HTTPS URL creative assets.
     * 0=No, 1=Yes. If omitted, the status is unknown.
     */
    @JsonProperty("secure")
    private Integer secure;

    /**
     * 1 if the ad is interstitial or full screen, else 0.
     */
    @JsonProperty("instl")
    private Integer instl;

    /**
     * Array of blocked creative attributes (OpenRTB List 5.3).
     */
    @JsonProperty("battr")
    private List<Integer> battr;

    /**
     * Name of ad mediation partner, SDK technology, or player responsible
     * for rendering ad (typically video or mobile). Used by some Ad Servers.
     */
    @JsonProperty("displaymanager")
    private String displayManager;

    /** Version of the display manager. */
    @JsonProperty("displaymanagerver")
    private String displayManagerVer;

    /**
     * Convenience: returns the format type of this impression.
     */
    public String resolveFormat() {
        if (video != null)     return "video";
        if (nativeObj != null) return "native";
        if (banner != null)    return "banner";
        return "unknown";
    }

    /**
     * Convenience: returns effective width of the impression.
     */
    public Integer effectiveWidth() {
        if (banner != null && banner.getW() != null) return banner.getW();
        if (video != null && video.getW() != null)   return video.getW();
        return null;
    }

    /**
     * Convenience: returns effective height of the impression.
     */
    public Integer effectiveHeight() {
        if (banner != null && banner.getH() != null) return banner.getH();
        if (video != null && video.getH() != null)   return video.getH();
        return null;
    }
}
