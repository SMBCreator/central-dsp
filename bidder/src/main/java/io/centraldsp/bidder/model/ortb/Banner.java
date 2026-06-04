package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.6 — Banner object.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Banner {

    /**
     * Array of format objects representing the banner sizes permitted.
     * If none, fall back to w/h.
     */
    @JsonProperty("format")
    private List<Format> format;

    /** Exact width in device independent pixels (DIPS). */
    @JsonProperty("w")
    private Integer w;

    /** Exact height in device independent pixels (DIPS). */
    @JsonProperty("h")
    private Integer h;

    /**
     * Blocked banner ad types (OpenRTB List 5.2).
     * 1=XHTML Text Ad, 2=XHTML Banner Ad, 3=JavaScript Ad, 4=iframe.
     */
    @JsonProperty("btype")
    private List<Integer> btype;

    /**
     * Blocked creative attributes (OpenRTB List 5.3).
     */
    @JsonProperty("battr")
    private List<Integer> battr;

    /**
     * Ad position on screen (OpenRTB List 5.4).
     * 0=Unknown, 1=Above the Fold, 3=Below the Fold, 4=Header, 5=Footer, 6=Sidebar, 7=Full Screen.
     */
    @JsonProperty("pos")
    private Integer pos;

    /** Unique identifier for this banner object. */
    @JsonProperty("id")
    private String id;

    /**
     * Inner class for banner format dimensions.
     * OpenRTB 2.5 §3.2.10.
     */
    @Data
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Format {
        @JsonProperty("w")
        private Integer w;

        @JsonProperty("h")
        private Integer h;
    }
}
