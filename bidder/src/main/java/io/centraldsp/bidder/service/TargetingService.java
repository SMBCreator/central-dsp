package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import io.centraldsp.bidder.model.campaign.CreativeDTO;
import io.centraldsp.bidder.model.campaign.TargetingDTO;
import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.Impression;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Targeting evaluation service.
 */
@Slf4j
@Service
public class TargetingService {

    public Optional<CreativeDTO> match(CampaignDTO campaign, BidRequest request, Impression imp) {
        TargetingDTO t = campaign.getTargeting();

        if (t != null) {
            if (!checkGeo(t, request))        return Optional.empty();
            if (!checkDevice(t, request))     return Optional.empty();
            if (!checkDomain(t, request))     return Optional.empty();
            if (!checkSchedule(t))            return Optional.empty();
        }

        return campaign.getCreatives().stream()
                .filter(CreativeDTO::isServable)
                .filter(c -> matchesImpression(c, imp))
                .findFirst();
    }

    // ── Geo ───────────────────────────────────────────────────────────────

    private boolean checkGeo(TargetingDTO t, BidRequest request) {
        List<String> allowedCountries = t.getGeoCountries();
        if (allowedCountries == null || allowedCountries.isEmpty()) return true;

        // Extract country into a single effectively-final local variable
        // by using a helper method — avoids "not effectively final" lambda error.
        final String country = resolveCountry(request);

        // If no geo signal, fail-open.
        if (country == null) return true;

        return allowedCountries.stream()
                .anyMatch(c -> c.equalsIgnoreCase(country));
    }

    /** Extract country code from device.geo or user.geo. */
    private String resolveCountry(BidRequest request) {
        if (request.getDevice() != null && request.getDevice().getGeo() != null
                && request.getDevice().getGeo().getCountry() != null) {
            return request.getDevice().getGeo().getCountry();
        }
        if (request.getUser() != null && request.getUser().getGeo() != null) {
            return request.getUser().getGeo().getCountry();
        }
        return null;
    }

    // ── Device type / OS / Browser ───────────────────────────────────────────

    private boolean checkDevice(TargetingDTO t, BidRequest request) {
        if (request.getDevice() == null) return true;

        if (t.getDeviceTypes() != null && !t.getDeviceTypes().isEmpty()) {
            Integer deviceType = request.getDevice().getDeviceType();
            if (deviceType != null) {
                String deviceLabel = mapDeviceType(deviceType);
                if (deviceLabel != null && !containsIgnoreCase(t.getDeviceTypes(), deviceLabel)) {
                    return false;
                }
            }
        }

        if (t.getOsList() != null && !t.getOsList().isEmpty()) {
            String os = request.getDevice().getOs();
            if (os != null && !containsIgnoreCase(t.getOsList(), os)) {
                return false;
            }
        }

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
        if (domain == null) return true;

        if (t.getSiteBlacklist() != null && !t.getSiteBlacklist().isEmpty()) {
            if (containsIgnoreCase(t.getSiteBlacklist(), domain)) {
                log.debug("[Targeting] Domain {} on blacklist", domain);
                return false;
            }
        }

        if (t.getSiteWhitelist() != null && !t.getSiteWhitelist().isEmpty()) {
            if (!containsIgnoreCase(t.getSiteWhitelist(), domain)) {
                log.debug("[Targeting] Domain {} not on whitelist", domain);
                return false;
            }
        }

        return true;
    }

    // ── Dayparting ────────────────────────────────────────────────────────────

    private boolean checkSchedule(TargetingDTO t) {
        if (t.getHourSchedule() == null || t.getHourSchedule().isEmpty()) return true;

        ZonedDateTime now = ZonedDateTime.now(ZoneOffset.UTC);
        int dayOfWeek = now.getDayOfWeek().getValue() - 1;
        int hour = now.getHour();

        String dayKey = String.valueOf(dayOfWeek);
        String hourKey = String.valueOf(hour);

        var daySchedule = t.getHourSchedule().get(dayKey);
        if (daySchedule == null) return false;

        Double multiplier = daySchedule.get(hourKey);
        if (multiplier == null) return false;

        return multiplier > 0.0;
    }

    // ── Creative <-> Impression matching ─────────────────────────────────────

    private boolean matchesImpression(CreativeDTO creative, Impression imp) {
        String format = creative.getFormat();
        if (format == null) return false;

        return switch (format) {
            case "banner" -> matchesBanner(creative, imp);
            case "native" -> imp.getNativeObj() != null;
            case "video"  -> imp.getVideo()     != null;
            default       -> false;
        };
    }

    private boolean matchesBanner(CreativeDTO creative, Impression imp) {
        if (imp.getBanner() == null) return false;

        Integer w = imp.getBanner().getW();
        Integer h = imp.getBanner().getH();

        if (w != null && h != null
                && creative.getWidth() != null && creative.getHeight() != null) {
            return creative.getWidth().equals(w) && creative.getHeight().equals(h);
        }

        if (imp.getBanner().getFormat() != null && !imp.getBanner().getFormat().isEmpty()
                && creative.getWidth() != null && creative.getHeight() != null) {
            return imp.getBanner().getFormat().stream().anyMatch(
                    f -> creative.getWidth().equals(f.getW())
                            && creative.getHeight().equals(f.getH()));
        }

        return true;
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private String resolveDomain(BidRequest request) {
        if (request.getSite() != null) {
            if (request.getSite().getDomain() != null) return request.getSite().getDomain();
            if (request.getSite().getPage() != null) {
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
            case 1 -> "mobile";
            case 2 -> "desktop";
            case 3 -> "ctv";
            case 4 -> "mobile";
            case 5 -> "tablet";
            case 6 -> "mobile";
            case 7 -> "ctv";
            default -> null;
        };
    }

    private boolean containsIgnoreCase(List<String> list, String value) {
        return list.stream().anyMatch(s -> s.equalsIgnoreCase(value));
    }
}
