// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface ISSVNetwork {
    struct Cluster {
        uint32 validatorCount;
        uint64 netMultiplier;
        address owner;
        int256 balance;
        uint64 activeBalance;
        uint64 index;
        bool active;
    }

    function migrateClusterToETH(uint64[] calldata operatorIds, Cluster calldata cluster) external;
}

interface ISSVNetworkViews {
    function getCluster(uint64[] calldata operatorIds, address owner) external view returns (ISSVNetwork.Cluster memory);
}

contract MigrateLiquidatedClusterTest is Test {
    ISSVNetwork ssvNetwork = ISSVNetwork(0xDD9BC35aE942eF0cFa76930954a156B3fF30a4E1);
    IERC20 ssvToken = IERC20(0x9D65fF81a3c488d585bBfb0Bfe3c7707c7917f54);
    ISSVNetworkViews ssvViews = ISSVNetworkViews(0xafE830B6Ee262ba11cce5F32fDCd760FFE6a66e4);

    function setUp() public {
        vm.createSelectFork("mainnet", 26_305_727);
    }

    function test_MigrateLiquidatedClusterSSVWipe() public {
        address clusterOwner = 0xe2758939507b35A22C30039A7da04B54cdc4c9A0;
        
        uint64[] memory operatorIds = new uint64[](4);
        operatorIds[0] = 1647;
        operatorIds[1] = 1648;
        operatorIds[2] = 1649;
        operatorIds[3] = 1650;

        // Fetch the live cluster struct from the fork
        ISSVNetwork.Cluster memory cluster = ssvViews.getCluster(operatorIds, clusterOwner);

        // Sanity checks on cluster state before migration
        assertEq(cluster.active, false, "Target cluster should be liquidated (inactive)");
        
        vm.startPrank(clusterOwner);
        
        uint256 balanceBefore = ssvToken.balanceOf(clusterOwner);
        console2.log("--- BEFORE MIGRATION ---");
        console2.log("Cluster Balance (internal struct):", cluster.balance);
        console2.log("Owner SSV Token Balance:", balanceBefore);

        // Execute migration
        ssvNetwork.migrateClusterToETH(operatorIds, cluster);
        
        uint256 balanceAfter = ssvToken.balanceOf(clusterOwner);
        console2.log("--- AFTER MIGRATION ---");
        console2.log("Owner SSV Token Balance:", balanceAfter);

        uint256 delta = balanceBefore > balanceAfter ? balanceBefore - balanceAfter : balanceAfter - balanceBefore;
        console2.log("Token Delta:", delta);

        // If the balance did not increase to refund the leftover internal balance, or if it dropped:
        // We log the exact finding.
        vm.stopPrank();
    }
}
