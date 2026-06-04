package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import io.centraldsp.bidder.model.campaign.CreativeDTO;
import io.centraldsp.bidder.model.campaign.TargetingDTO;
import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.Impression;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.DayOfWeek;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Targeting evaluation service.
 *
 * <p>For a campaign + BidRequest + Impression triple, determines:
 * <ol>
 *   <li>Does the campaign's targeting rules match this request?</li>
 *   <li>Which creative is the best match for this impression?</li>
 * </ol>
 * </p>
 *
 * <p>All checks are fail-open when data is missing:
 * if the SSP doesn't send geo/device info we assume it passes.
 * This maximizes fill rate for partial signals.</p>
 */
@Slf4j
@Service
public class TargetingService {

    /**
     * Main entry point. Returns the best matching creative,
     * or empty if this campaign should not bid on this impression.
     *
     * @param campaign   Campaign with targeting rules and creatives
     * @param request    OpenRTB BidRequest
     * @param imp        The specific Impression being evaluated
     * @return Optional with the best matching CreativeDTO, or empty
     */
    public Optional<CreativeDTO> match(CampaignDTO campaign, BidRequest request, Impression imp) {
        TargetingDTO t = campaign.getTargeting();

        // No targeting rules = match everything.
        if (t != null) {
            if (!checkGeo(t, request))        return Optional.empty();
            if (!checkDevice(t, request))     return Optional.empty();
            if (!checkDomain(t, request))     return Optional.empty();
            if (!checkSchedule(t))            return Optional.empty();
        }

        // Find first creative that matches impression dimensions & format.
        return campaign.getCreatives().stream()
                .filter(CreativeDTO::isServable)
                .filter(c -> matchesImpression(c, imp))
                .findFirst();
    }

    // ── Geo ───────────────────────────────────────────────────────────────────

    private boolean checkGeo(TargetingDTO t, BidRequest request) {
        List<String> allowedCountries = t.getGeoCountries();
        if (allowedCountries == null || allowedCountries.isEmpty()) return true;

        String country = null;
        if (request.getDevice() != null && request.getDevice().getGeo() != null) {
            country = request.getDevice().getGeo().getCountry();
        } else if (request.getUser() != null && request.getUser().getGeo() != null) {
            country = request.getUser().getGeo().getCountry();
        }

        // If no geo signal, fail-open.
        if (country == null) return true;

        return allowedCountries.stream()
                .anyMatch(c -> c.equalsIgnoreCase(country));
    }

    // ── Device type / OS / Browser ───────────────────────────────────────────

    private boolean checkDevice(TargetingDTO t, BidRequest request) {
        if (request.getDevice() == null) return true;

        // Device type check
        if (t.getDeviceTypes() != null && !t.getDeviceTypes().isEmpty()) {
            Integer deviceType = request.getDevice().getDevicetype();
            if (deviceType != null) {
                // OpenRTB device types: 1=mobile/tablet, 2=PC, 3=CTV, 4=phone,
                // 5=tablet, 6=connected device, 7=set top box
                String deviceLabel = mapDeviceType(deviceType);
                if (deviceLabel != null && !containsIgnoreCase(t.getDeviceTypes(), deviceLabel)) {
                    return false;
                }
            }
        }

        // OS check
        if (t.getOsList() != null && !t.getOsList().isEmpty()) {
            String os = request.getDevice().getOs();
            if (os != null && !containsIgnoreCase(t.getOsList(), os)) {
                return false;
            }
        }

        // Browser check
        if (t.getBrowserList() != null && !t.getBrowserList().isEmpty()) {
            String browser = request.getDevice().getBrowser();
            if (browser != null && !containsIgnoreCase(t.getBrowserList(), browser)) {
                return false;
            }
        }

        return true;
    }

    // ── Domain whitelist / blacklist ─────────────────────────────────────────

    private boolean checkDomain(TargetingDTO t, BidRequest request) {
        String domain = resolveDomain(request);
        if (domain == null) return true; // No domain signal = fail-open

        // Blacklist: reject if domain is on blocklist
        if (t.getSiteBlacklist() != null && !t.getSiteBlacklist().isEmpty()) {
            if (containsIgnoreCase(t.getSiteBlacklist(), domain)) {
                log.debug("[Targeting] Domain {} on blacklist", domain);
                return false;
            }
        }

        // Whitelist: reject if whitelist is set and domain is NOT on it
        if (t.getSiteWhitelist() != null && !t.getSiteWhitelist().isEmpty()) {
            if (!containsIgnoreCase(t.getSiteWhitelist(), domain)) {
                log.debug("[Targeting] Domain {} not on whitelist", domain);
                return false;
            }
        }

        return true;
    }

    // ── Dayparting (hour-of-week schedule) ───────────────────────────────────

    private boolean checkSchedule(TargetingDTO t) {
        if (t.getHourSchedule() == null || t.getHourSchedule().isEmpty()) return true;

        ZonedDateTime now = ZonedDateTime.now(ZoneOffset.UTC);
        // Day of week: Mon=0 .. Sun=6 (matches Python's weekday())
        int dayOfWeek = now.getDayOfWeek().getValue() - 1; // DayOfWeek.MONDAY = 1
        int hour = now.getHour();

        String dayKey = String.valueOf(dayOfWeek);
        String hourKey = String.valueOf(hour);

        var daySchedule = t.getHourSchedule().get(dayKey);
        if (daySchedule == null) return false; // Day not in schedule = no bid

        Double multiplier = daySchedule.get(hourKey);
        if (multiplier == null) return false; // Hour not in schedule = no bid

        return multiplier > 0.0; // Multiplier 0 = paused for this hour
    }

    // ── Creative ↔ Impression matching ───────────────────────────────────────

    private boolean matchesImpression(CreativeDTO creative, Impression imp) {
        String format = creative.getFormat();
        if (format == null) return false;

        return switch (format) {
            case "banner" -> matchesBanner(creative, imp);
            case "native" -> imp.getNative() != null; // Any native imp
            case "video"  -> imp.getVideo()  != null; // Any video imp
            default       -> false;
        };
    }

    private boolean matchesBanner(CreativeDTO creative, Impression imp) {
        if (imp.getBanner() == null) return false;

        // If SSP specifies exact dimensions, match them.
        Integer w = imp.getBanner().getW();
        Integer h = imp.getBanner().getH();

        if (w != null && h != null
                && creative.getWidth() != null && creative.getHeight() != null) {
            return creative.getWidth().equals(w) && creative.getHeight().equals(h);
        }

        // If SSP sends a format list, check it.
        if (imp.getBanner().getFormat() != null && !imp.getBanner().getFormat().isEmpty()
                && creative.getWidth() != null && creative.getHeight() != null) {
            return imp.getBanner().getFormat().stream().anyMatch(
                    f -> creative.getWidth().equals(f.getW())
                            && creative.getHeight().equals(f.getH()));
        }

        // No dimension constraints = accept any banner creative.
        return true;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private String resolveDomain(BidRequest request) {
        if (request.getSite() != null) {
            if (request.getSite().getDomain() != null) return request.getSite().getDomain();
            if (request.getSite().getPage() != null) {
                // Extract domain from page URL
                try {
                    String page = request.getSite().getPage();
                    int slashSlash = page.indexOf("//");
                    if (slashSlash >= 0) {
                        String rest = page.substring(slashSlash + 2);
                        int slash = rest.indexOf('/');
                        return slash > 0 ? rest.substring(0, slash) : rest;
                    }
                } catch (Exception ignored) {}
            }
        }
        if (request.getApp() != null && request.getApp().getDomain() != null) {
            return request.getApp().getDomain();
        }
        return null;
    }

    private String mapDeviceType(int ortbType) {
        return switch (ortbType) {
            case 1 -> "mobile";    // Mobile/tablet (legacy)
            case 2 -> "desktop";   // Personal computer
            case 3 -> "ctv";       // Connected TV
            case 4 -> "mobile";    // Phone
            case 5 -> "tablet";    // Tablet
            case 6 -> "mobile";    // Connected device
            case 7 -> "ctv";       // Set top box
            default -> null;
        };
    }

    private boolean containsIgnoreCase(List<String> list, String value) {
        return list.stream().anyMatch(s -> s.equalsIgnoreCase(value));
    }
}
