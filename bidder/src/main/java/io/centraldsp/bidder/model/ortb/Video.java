package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.7 — Video object.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Video {

    /**
     * Content MIME types supported (e.g. "video/mp4").
     */
    @JsonProperty("mimes")
    private List<String> mimes;

    /** Minimum video ad duration in seconds. */
    @JsonProperty("minduration")
    private Integer minDuration;

    /** Maximum video ad duration in seconds. */
    @JsonProperty("maxduration")
    private Integer maxDuration;

    /**
     * Supported video bid response protocols (OpenRTB List 5.8).
     * 2=VAST 2.0, 3=VAST 3.0, 5=VAST 2.0 Wrapper, 6=VAST 3.0 Wrapper.
     */
    @JsonProperty("protocols")
    private List<Integer> protocols;

    /** Width of the player in device independent pixels (DIPS). */
    @JsonProperty("w")
    private Integer w;

    /** Height of the player in device independent pixels (DIPS). */
    @JsonProperty("h")
    private Integer h;

    /**
     * Indicates if the impression must be linear (1=Linear, 2=Non-linear, 3=Both).
     */
    @JsonProperty("linearity")
    private Integer linearity;

    /**
     * Indicates if the player can be skipped (0=No, 1=Yes).
     */
    @JsonProperty("skip")
    private Integer skip;

    /**
     * Videos of total duration > skipmin may be skippable (in seconds).
     */
    @JsonProperty("skipmin")
    private Integer skipMin;

    /**
     * Number of seconds a video must play before skipping is enabled.
     */
    @JsonProperty("skipafter")
    private Integer skipAfter;

    /**
     * Ad position (same as Banner.pos).
     */
    @JsonProperty("pos")
    private Integer pos;

    /**
     * Blocked creative attributes.
     */
    @JsonProperty("battr")
    private List<Integer> battr;

    /**
     * Supported playback methods (OpenRTB List 5.10).
     */
    @JsonProperty("playbackmethod")
    private List<Integer> playbackMethod;
}
