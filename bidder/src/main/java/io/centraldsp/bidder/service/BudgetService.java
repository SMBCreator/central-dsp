package io.centraldsp.bidder.service;

import io.centraldsp.bidder.model.campaign.CampaignDTO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import redis.clients.jedis.Jedis;
import redis.clients.jedis.JedisPool;
import redis.clients.jedis.JedisPoolConfig;

import jakarta.annotation.PostConstruct;
import java.time.LocalDate;

/**
 * Budget enforcement using Redis counters.
 *
 * <p>Key pattern: {@code cdsp:budget:{campaignId}:{YYYY-MM-DD}}
 * TTL = 25 hours (covers timezone overlap safely).</p>
 *
 * <p>Fail-open policy: on Redis errors we allow bids to continue
 * rather than blocking revenue. Errors are logged for ops alerts.</p>
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class BudgetService {

    @Value("${redis.host:redis}")
    private String redisHost;

    @Value("${redis.port:6379}")
    private int redisPort;

    @Value("${redis.db:1}")
    private int redisDb;

    private JedisPool jedisPool;

    private static final String KEY_PREFIX = "cdsp:budget:";
    private static final long DAILY_TTL_SECONDS = 90_000L; // 25 hours

    @PostConstruct
    public void init() {
        JedisPoolConfig config = new JedisPoolConfig();
        config.setMaxTotal(20);
        config.setMaxIdle(5);
        config.setMinIdle(1);
        jedisPool = new JedisPool(config, redisHost, redisPort);
        log.info("[BudgetService] Connected to Redis {}:{}", redisHost, redisPort);
    }

    /** Initialize daily budget counter if not exists. */
    public void initBudget(CampaignDTO campaign) {
        String key = dailyKey(campaign.getId());
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            Long result = jedis.setnx(key, "0");
            if (result == 1) {
                jedis.expire(key, DAILY_TTL_SECONDS);
            }
        } catch (Exception e) {
            log.error("[BudgetService] initBudget failed for campaign {}: {}", campaign.getId(), e.getMessage());
        }
    }

    /**
     * Check if campaign has remaining daily budget.
     *
     * @param campaign  Campaign to check
     * @param bidPrice  Proposed bid price in CPM USD
     * @return true if budget is available (fail-open on Redis errors)
     */
    public boolean hasBudget(CampaignDTO campaign, double bidPrice) {
        if (campaign.getDailyBudget() == null) return true;
        String key = dailyKey(campaign.getId());
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            double spent = val != null ? Double.parseDouble(val) : 0.0;
            return (spent + bidPrice) <= campaign.getDailyBudget();
        } catch (Exception e) {
            log.warn("[BudgetService] hasBudget check error: {}", e.getMessage());
            return true; // fail-open
        }
    }

    /**
     * Atomically increment daily spend when a bid wins.
     *
     * <p>Called from WinLossController with actual clearing price.</p>
     *
     * @param campaignId  Campaign ID
     * @param amountUsd   Amount to deduct in USD (NOT CPM - actual cost per impression)
     */
    public void deductBudget(Long campaignId, double amountUsd) {
        recordSpend(campaignId, amountUsd);
    }

    /**
     * Alias kept for backwards compatibility with internal callers.
     *
     * @param campaignId Campaign ID
     * @param winPrice   CPM price (will be recorded as-is; divide by 1000 before calling
     *                   if you want per-impression cost)
     */
    public void recordSpend(Long campaignId, double winPrice) {
        String key = dailyKey(campaignId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            jedis.incrbyfloat(key, winPrice);
            if (jedis.ttl(key) < 0) {
                jedis.expire(key, DAILY_TTL_SECONDS);
            }
        } catch (Exception e) {
            log.error("[BudgetService] recordSpend failed for campaign {}: {}", campaignId, e.getMessage());
        }
    }

    /** Get today's accumulated spend for a campaign in USD. */
    public double getDailySpend(Long campaignId) {
        String key = dailyKey(campaignId);
        try (Jedis jedis = jedisPool.getResource()) {
            jedis.select(redisDb);
            String val = jedis.get(key);
            return val != null ? Double.parseDouble(val) : 0.0;
        } catch (Exception e) {
            log.warn("[BudgetService] getDailySpend error for campaign {}: {}", campaignId, e.getMessage());
            return 0.0;
        }
    }

    private String dailyKey(Long campaignId) {
        return KEY_PREFIX + campaignId + ":" + LocalDate.now();
    }
}
